/**
 * Atmosphere: the two distance cues correct geometry cannot supply.
 *
 * A fog BANK is geometry — things wade in it, which `scene.fog` can
 * never do (it is a function of camera distance, so it veils a figure's
 * head exactly as much as its boots). Aerial perspective is the COLOUR
 * half of distance: FogExp2 dims but never shifts hue, and without the
 * shift a correctly built scene still reads as a tabletop model.
 */

import * as THREE from 'three';
import { mulberry32 } from './noise.js';
import { makeShaderMaterial, patchStandard, tickShaders } from './shader.js';

// Sheets are packed toward the ground (h ~ f^PACK): an evenly spaced
// stack spends most of its geometry in the thin top half where there
// is almost nothing to draw. BASE_H keeps the first sheet off the soil.
//
// LAYERS is the QUANTISATION of the height integral, and it shows: a
// sheet's plane crosses anything vertical standing in the bank along a
// horizontal line, and across that line the count of sheets in front
// changes by one. At 12 sheets a crate in the bank wore six hard
// contour bands (measured on this renderer: six steps over its front
// face, the largest 7/255). Twenty sheets halve the step, and the
// noise floor + dither in the fragment stage break what is left.
const LAYERS = 20;
const PACK = 1.6;
const BASE_H = 0.03;
// Metres of the bank's thickest air that `density` is quoted over, so
// the dial means one physical thing at any `extent`, `height` or
// LAYERS. Written into the GLSL below as 1/REF_M.
const REF_M = 10;
// environment.js MOODS.day.horizon — the fallback for a bank built with
// no scene to read. Not a light colour anyone should be relying on:
// pass `scene` (or `color`) and the bank takes the scene's own.
const DAY_HORIZON = 0xdbe3ea;
// Henyey-Greenstein asymmetry. Water droplets scatter hard forward, so
// a bank you look THROUGH toward the sun is a light source and the same
// bank with the sun behind you is a grey wall. HG_NORM normalises the
// broadside value to 1 so `uSunAmt` alone sets the strength.
const HG_G = 0.55;
const HG_G2 = HG_G * HG_G;
const HG_NORM = ((1 - HG_G2) / Math.pow(1 + HG_G2, 1.5)).toFixed(6);
// Radiance the key light leaves in a metre of the bank's thickest air,
// per unit of three's directional intensity. Tuned on this renderer
// against the moon rig (2.2) and the day rig (5.4).
const BEAM_K = 0.022;

const LUMA = 'vec3(0.2126, 0.7152, 0.0722)';
/** One horizon read per baked environment, not per patched material. */
const AIR_CACHE = new WeakMap();

/** Luminance of a linear-space THREE.Color. */
function lumOf(c) {
  return Math.max(1e-4, 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b);
}

/** `c`'s chromaticity at unit luminance — hue without brightness. */
function chroma(c) {
  return c.clone().multiplyScalar(1 / lumOf(c));
}

/**
 * The radiance of the air itself, read off the baked environment.
 *
 * In-scattered light IS the colour of fog, and the scene already
 * carries it: `sunRig` bakes the same graded sky the dome draws into a
 * linear half-float equirect. The band from the horizon to 25 degrees
 * up is what a ground-level ray flies through, so its radiance is the
 * bank's colour and the far field's airlight — in every mood, with no
 * constant of mine anywhere near it. Measured off this bake: day
 * (0.436, 0.764, 1.082), a real sky blue rather than the near-white
 * `scene.fog` carries; night (0.026, 0.036, 0.073), seven times the
 * night mood's fog colour, which is set to swallow the far field and
 * is far too dark to be air.
 *
 * The middle two quartiles, not the mean: at a low sun the bake's HDR
 * sun disc (radiance 5) sits inside this band and doubles a mean
 * (golden reads 0.505 flat, 0.262 trimmed).
 *
 * @param {THREE.Scene} [scene]
 * @returns {THREE.Color|null} null unless `scene.environment` is a
 *   linear equirect DataTexture (a PMREM cube, an LDR texture or no
 *   environment at all falls back to the fog colour).
 */
function horizonRadiance(scene) {
  const tex = scene && scene.environment;
  const img = tex && tex.image;
  const data = img && img.data;
  if (!data || tex.mapping !== THREE.EquirectangularReflectionMapping) {
    return null;
  }
  // Per BAKE, not per call: `patchAerialPerspective` is called once per
  // material and every one of them would otherwise re-read a quarter of
  // the equirect. The cached colour is never handed out — callers of
  // `readScene` mutate what they get.
  if (AIR_CACHE.has(tex)) {
    const hit = AIR_CACHE.get(tex);
    return hit ? hit.clone() : null;
  }
  const half = tex.type === THREE.HalfFloatType;
  const w = img.width | 0;
  const h = img.height | 0;
  const stride = w && h ? data.length / (w * h) : 0;
  if ((!half && tex.type !== THREE.FloatType)
      || w < 4 || h < 4 || (stride !== 3 && stride !== 4)) {
    AIR_CACHE.set(tex, null);
    return null;
  }
  const f = half ? THREE.DataUtils.fromHalfFloat : (v) => v;
  // three samples an equirect at v = 0.5 + asin(dir.y) / PI and a
  // DataTexture never flips Y, so v 0.500 is the horizon and 0.639 is
  // 25 degrees up.
  const y1 = Math.min(h, Math.round(0.639 * h));
  const px = [];
  for (let y = Math.round(0.5 * h); y < y1; y++) {
    for (let x = 0; x < w; x += 2) {
      const i = (y * w + x) * stride;
      const c = [f(data[i]), f(data[i + 1]), f(data[i + 2])];
      px.push([0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2], c]);
    }
  }
  if (px.length < 8) {
    AIR_CACHE.set(tex, null);
    return null;
  }
  px.sort((a, b) => a[0] - b[0]);
  const slice = px.slice(Math.floor(px.length * 0.25),
                         Math.ceil(px.length * 0.75));
  const acc = new THREE.Color(0, 0, 0);
  for (const [, c] of slice) { acc.r += c[0]; acc.g += c[1]; acc.b += c[2]; }
  acc.multiplyScalar(1 / slice.length);
  AIR_CACHE.set(tex, acc);
  return acc.clone();
}

/**
 * Air is never darker than the light standing in it.
 *
 * `scene.fog.color` is doing TWO jobs and they part company at night:
 * it also has to swallow the far field, so a night mood sets it near
 * black (0x0b0f1a — luminance 0.005 linear) while the air in front of
 * you is still carrying whatever the sky and the moon put into it. Used
 * raw, the far field crushes to a black-blue silhouette (measured: a
 * ridge 70 m out fell to (9, 23, 50) against a (26, 36, 64) sky).
 * Below `floor` the colour is pulled toward `ref`'s hue and lifted to
 * it: dark, but lit, and blue rather than nothing.
 *
 * @param {THREE.Color} color Mutated and returned.
 * @param {THREE.Color|null} ref The hue to fall back toward (the air's
 *   own radiance, else the fill light).
 * @param {number} floor Luminance floor, linear.
 */
function inScatterFloor(color, ref, floor) {
  if (!ref || !(floor > 0) || lumOf(color) >= floor) return color;
  color.lerp(ref, 0.6);
  return color.multiplyScalar(floor / lumOf(color));
}

/**
 * What the scene says about its own air and its own light.
 *
 * Atmosphere is the one effect that may not carry colours of its own:
 * a bank at a baked-in daylight white is a slab of milk in a night
 * scene (measured here: the ground under it read (69,66,64) against a
 * (26,36,64) sky — the brightest thing in the frame). So the colour of
 * the air comes from `scene.environment`, the fall-backs from
 * `scene.fog`, the warm half from the scene's own key light and the
 * cool half from its fill.
 *
 * @param {THREE.Scene} [scene]
 * @returns {{air: THREE.Color|null, fog: THREE.Color|null,
 *   dir: THREE.Vector3|null, sun: THREE.Color|null, key: number,
 *   amb: THREE.Color|null, ambLum: number, fogDensity: number}}
 */
function readScene(scene) {
  const out = { air: null, fog: null, dir: null, sun: null, key: 0,
                amb: null, ambLum: 0, fogDensity: 0 };
  if (!scene || typeof scene.traverse !== 'function') return out;
  out.air = horizonRadiance(scene);
  if (scene.fog && scene.fog.color) {
    out.fog = scene.fog.color.clone();
    out.fogDensity = scene.fog.density || 0;
  }
  let best = null;
  let fill = null;
  scene.traverse((o) => {
    if (o.isDirectionalLight) {
      if (!best || o.intensity > best.intensity) best = o;
    } else if (o.isHemisphereLight || o.isAmbientLight) {
      if (!fill || o.intensity > fill.intensity) fill = o;
    }
  });
  if (best) {
    // sunRig aims its light at the origin, so the position IS the
    // vector to the light — and at night that is the MOON, which is
    // what a bank actually scatters. An authored `sunDir` from a set
    // sun would point under the ground.
    out.dir = best.position.clone();
    if (out.dir.lengthSq() < 1e-8) out.dir = null;
    else out.dir.normalize();
    out.sun = best.color.clone();
    out.key = best.intensity;
  }
  if (fill) {
    out.amb = fill.color.clone();
    out.ambLum = lumOf(fill.color) * fill.intensity;
  }
  return out;
}

const FOG_VERT = [
  '  vPos = transformed;',
  // The slope of the view ray decides how much air a sheet stands for,
  // and the profile is in WORLD metres of height, so the ray must be
  // measured in world space too.
  '  vWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;',
  '  vH = aH;',
  '  vDH = aDH;',
].join('\n');

const FOG_HEAD = [
  'uniform vec3 uColor;',
  'uniform vec3 uSunTint;',
  'uniform vec3 uAmbTint;',
  'uniform vec3 uBeam;',
  'uniform vec3 uSunDir;',
  'uniform float uSunAmt;',
  'uniform float uDensity;',
  'uniform float uTop;',
  'uniform float uScale;',
  'uniform float uFreq;',
  'uniform float uRadius;',
  'uniform vec2 uOffset;',
].join('\n');

const FOG_FRAG = [
  // Density falls with HEIGHT in metres and is forced to exactly zero
  // at uTop; a bank that merely gets thin has a visible lid.
  '  float prof = exp(-vH / uScale)',
  '      * (1.0 - smoothstep(uTop * 0.45, uTop, vH));',
  // Feather the rim INSIDE the geometry, or a transparent sheet draws
  // its own rectangle across the frame.
  '  float edge = 1.0 - smoothstep(0.60, 1.0,',
  '      length(vPos.xz) / uRadius);',
  // METRES OF AIR this sheet stands for: its own slab crossed at the
  // view ray's slope, never more than the ray has flown. Ignore the
  // slope and the ground at your feet is charged the whole column.
  '  vec3 ray = vWorld - cameraPosition;',
  '  float dist = length(ray);',
  '  float climb = max(abs(ray.y) / dist, 1e-3);',
  '  float air = min(vDH / climb, dist) * prof * edge;',
  // Extinction per metre at the foot of the bank, from the dial.
  `  float sigma = -log(1.0 - min(uDensity, 0.995)) / ${REF_M}.0;`,
  '  if (sigma * air < 0.0015) discard;',
  // Offset the noise domain by height: without it every sheet wears the
  // same blotches and the stack reads as one printed slab.
  '  vec2 p = vPos.xz * uFreq + uOffset + vec2(vH * 0.31, vH * -0.22);',
  '  p += vec2(uTime * 0.015, uTime * -0.009);',
  '  float n = smoothstep(0.20, 0.80, astraFbm2(p, 4));',
  // Eaten by noise at the top; at the ground it keeps its MEAN (n runs
  // 0..1 about 0.5) but not its uniformity. A flat 1.0 down there was
  // the sheet stack's tell: every sheet drew one constant alpha, so
  // each sheet's plane printed a hard contour line across anything
  // standing in the bank. Patchy air makes the same line a ragged edge,
  // and real fog at the soil is patchy anyway.
  '  float low = 0.55 + 0.90 * n;',
  '  float breakup = mix(low, n, smoothstep(0.10, 0.85, vH / uTop));',
  // Beer-Lambert on that air, so the stack telescopes into ONE integral
  // however many sheets a ray meets: opacity 1 - exp(-sum of the taus).
  '  float a = 1.0 - exp(-sigma * air * breakup * (0.85 + 0.30 * n));',
  // A screen-space dither of half the remaining quantisation step: the
  // eye integrates the noise and stops finding the contour. Gated by
  // `a` so the thin outer wisps are not dusted with speckle.
  '  a += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.02',
  '      * smoothstep(0.0, 0.12, a);',
  '  if (a < 0.002) discard;',
  // COLOUR. Fog is not a paint value, it is the light in the air, and
  // it has to move: warm and bright looking through it toward the key
  // light, cool and flat looking away, brighter at the top of the bank
  // where the sky reaches than in the shadowed soil at the bottom.
  // uColor is the base (the scene's own fog tint); uSunTint/uAmbTint
  // are its two chromaticities at unit luminance, so these mixes move
  // HUE and the two literal gains own every change of brightness.
  `  float baseL = dot(uColor, ${LUMA});`,
  '  vec3 shadeC = mix(uColor, uAmbTint * baseL, 0.35) * 0.76;',
  '  vec3 litC = mix(uColor, uSunTint * baseL, 0.55) * 1.18;',
  '  vec3 V = ray / dist;',
  '  float ct = clamp(dot(V, uSunDir), -1.0, 1.0);',
  `  float hgD = 1.0 + ${HG_G2.toFixed(6)} - ${(2 * HG_G).toFixed(6)} * ct;`,
  `  float phase = clamp(((1.0 - ${HG_G2.toFixed(6)})`,
  `      / (hgD * sqrt(hgD))) / ${HG_NORM}, 0.75, 2.6);`,
  '  float sunMix = clamp(0.45 + uSunAmt * (phase - 1.0) * 0.35, 0.0, 1.0);',
  // The BEAM is added, not mixed: the sky already in `uColor` outruns
  // the moon twenty to one by day and only two to one at night, so a
  // multiplier tuned for one is invisible in the other. This term is
  // the key light's own radiance in the air, so a moonlit bank glows
  // (measured: 32/255, under the soil it covered, before this) while
  // the same code adds a modest warm haze at noon.
  '  vec3 tint = mix(shadeC, litC, sunMix) + uBeam * phase;',
  // The bank is lit from above and shadows itself downward, so the top
  // is the bright surface. Without it twenty sheets of one colour are
  // still one flat value.
  '  tint *= mix(0.88, 1.25, smoothstep(0.0, 0.75, vH / uTop));',
  // Patch-to-patch hue spread on the SAME field the density uses, so
  // the warm and cool blooms sit where the thick and thin air is.
  '  tint = astraHueBreak(tint, p, 0.55, 0.30);',
  '  gl_FragColor = vec4(tint, clamp(a, 0.0, 1.0));',
].join('\n');

/**
 * Stacked sheets: y follows the terrain, `aH` is height above it,
 * `aDH` the metres of column that sheet is the sample of.
 */
function fogSheets(extent, top, heightAt) {
  // Flat ground needs no tessellation at all — every variation lives in
  // the fragment stage, so the whole bank is then LAYERS quads.
  const seg = heightAt
      ? Math.max(8, Math.min(64, Math.round(extent / 1.5))) : 1;
  const row = seg + 1;
  const half = extent * 0.5;
  const pos = [];
  const hs = [];
  const dhs = [];
  const idx = [];
  const at = (l) => BASE_H
      + (top - BASE_H) * Math.pow(l / (LAYERS - 1), PACK);
  for (let l = 0; l < LAYERS; l++) {
    const h = at(l);
    // Half way down to the sheet below, half way up to the one above,
    // so the stack partitions the column [0, top] exactly once and no
    // metre of air is either counted twice or missed.
    const dh = (l < LAYERS - 1 ? (h + at(l + 1)) * 0.5 : top)
        - (l > 0 ? (at(l - 1) + h) * 0.5 : 0);
    const base = l * row * row;
    for (let j = 0; j < row; j++) {
      const z = -half + (extent * j) / seg;
      for (let i = 0; i < row; i++) {
        const x = -half + (extent * i) / seg;
        pos.push(x, (heightAt ? heightAt(x, z) : 0) + h, z);
        hs.push(h);
        dhs.push(dh);
      }
    }
    for (let j = 0; j < seg; j++) {
      for (let i = 0; i < seg; i++) {
        const a = base + j * row + i;
        idx.push(a, a + row, a + 1, a + 1, a + row, a + row + 1);
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('aH', new THREE.Float32BufferAttribute(hs, 1));
  geo.setAttribute('aDH', new THREE.Float32BufferAttribute(dhs, 1));
  geo.setIndex(idx);
  return geo;
}

/**
 * A ground-hugging fog bank as real geometry — things WADE in it.
 *
 * Density is a function of height above the ground, not of camera
 * distance: milk in the first metre, gone by `height`, so boots vanish
 * while heads stay clear. What a ray then COLLECTS is the air it flies
 * through, so the band is a thing you see across — faint at your feet,
 * where you look steeply down through a metre of it, and solid down the
 * valley, where the same metre lies along the sightline for a hundred.
 * Every sheet is one colour, which makes the over-blend order
 * independent, so the whole stack is one draw call with no sorting to
 * get wrong.
 *
 * PASS THE SCENE. The bank's colour is in-scattered light, never a
 * paint value: `scene` gives it the scene's own fog tint, its key
 * light's warmth on the sun side and its fill's cool in the shade, so
 * one call reads right at noon and at midnight. Without it the bank
 * falls back to a daylight horizon and a night scene gets a slab of
 * milk.
 *
 * @param {object} [opts]
 *   `scene` THREE.Scene to read (fog colour + the strongest
 *   DirectionalLight + the fill) — strongly recommended;
 *   `extent` metres square (default 60); `height` metres at which the
 *   bank is gone (default 4; `top` is the old name and still works);
 *   `density` how thick the air is — the opacity of a 10 m horizontal
 *   look through the bank's foot (default 0.75), so it means the same
 *   thing at any extent, height or sheet count; `color` THREE.Color
 *   overriding the base tint (default: the radiance of the scene's own
 *   air, read off `scene.environment`, then `scene.fog.color`, then the
 *   day mood horizon); `sunDir` Vector3 or [x, y, z] TOWARD the key
 *   light, overriding the one read from the scene; `sunAmount` how
 *   strongly the air scatters that light, 0..1 against a full sun (the
 *   day rig's 5.4 is 1.0, the moon's 2.2 is 0.44; default from the
 *   light itself); `heightAt` (x, z) => y to follow terrain —
 *   omit it and the bank is flat and costs 20 quads; `seed` noise
 *   offset.
 * @returns {THREE.Group} Named `HeightFog`, with `userData.tick(t)`
 *   driving the drift. Move the group to move the bank: the soft rim
 *   is inscribed in `extent`, so it never shows a boundary of its own.
 *   Retune later through `uniforms.uColor` / `uDensity` / `uSunAmt`.
 */
export function makeHeightFog(opts = {}) {
  const extent = opts.extent ?? 60;
  // Every other lib in here names this `height` (watermist, rain,
  // grass...), and a silently ignored `height` gave the default 4 m
  // bank to callers who asked for a 1.6 m one.
  const top = Math.max(0.2, opts.height ?? opts.top ?? 4);
  const density = opts.density ?? 0.75;
  const heightAt = opts.heightAt || null;
  const env = readScene(opts.scene);
  // The bank is a VOLUME of lit air standing in front of the camera,
  // so its colour is the radiance of the air, not the far-field tint
  // `scene.fog` carries. Moonlit mist is BRIGHTER than the ground it
  // lies on — the ground returns a fraction of the moon, the air
  // scatters it whole — and at the raw night fog colour this rendered
  // UNDER the soil it covered (28/255 over 45/255) and read as a stain.
  const color = opts.color !== undefined ? new THREE.Color(opts.color)
      : (env.air ? env.air.clone()
         : inScatterFloor(env.fog || new THREE.Color(DAY_HORIZON),
                          env.amb, 0.55 * env.ambLum));
  const sunDir = opts.sunDir
      ? (Array.isArray(opts.sunDir)
          ? new THREE.Vector3().fromArray(opts.sunDir)
          : new THREE.Vector3().copy(opts.sunDir)).normalize()
      : (env.dir || new THREE.Vector3(0, 1, 0));
  // `sunAmount` is quoted against a full sun (three's physical units:
  // the day rig runs 5.4, the moon 2.2), so one dial drives both the
  // hue mix and the beam.
  const keyI = opts.sunAmount === undefined ? env.key : opts.sunAmount * 5;
  const sunAmt = Math.min(1, Math.max(0, keyI / 5));
  const beam = (env.sun ? env.sun.clone() : new THREE.Color(0xffffff))
      .multiplyScalar(Math.max(0, keyI) * BEAM_K);
  const rand = mulberry32(opts.seed ?? 1);
  const mat = makeShaderMaterial({
    name: 'HeightFog',
    uniforms: {
      uColor: { value: color },
      uSunTint: { value: chroma(env.sun || color) },
      uAmbTint: { value: chroma(env.amb || color) },
      uBeam: { value: beam },
      uSunDir: { value: sunDir },
      uSunAmt: { value: sunAmt },
      uDensity: { value: density },
      uTop: { value: top },
      // ~1/4 of the bank is the e-fold, so the first metre carries most
      // of the extinction at the default height of 4 m.
      uScale: { value: top * 0.28 },
      uFreq: { value: 3 / extent },
      uRadius: { value: extent * 0.5 },
      uOffset: {
        value: new THREE.Vector2(rand() * 128, rand() * 128),
      },
    },
    varyings: 'varying vec3 vPos;\nvarying vec3 vWorld;\n'
        + 'varying float vH;\nvarying float vDH;',
    vertexHead: 'attribute float aH;\nattribute float aDH;',
    vertexMain: FOG_VERT,
    fragmentHead: FOG_HEAD,
    fragmentMain: FOG_FRAG,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(fogSheets(extent, top, heightAt), mat);
  mesh.name = 'HeightFogBank';
  // Drawn with an OVERRIDE material — GTAOPass's depth+normal buffer —
  // a dozen sheets read as a dozen solid floors occluding each other:
  // 50/255 of darkening on ground the fog was not even touching.
  mesh.onBeforeRender = (r, s, cam, geo, m) => {
    geo.setDrawRange(0, m === mat ? Infinity : 0);
  };
  mesh.onAfterRender = (r, s, cam, geo) => {
    geo.setDrawRange(0, Infinity);
  };
  const group = new THREE.Group();
  group.name = 'HeightFog';
  group.add(mesh);
  group.userData.tick = (t) => tickShaders(group, t);
  return group;
}

const AERIAL_VERT = [
  // three's own fog measures view-space Z (`vFogDepth = -mvPosition.z`),
  // so this uses the same ruler and the two cues stay locked. Instances
  // are transformed here because <begin_vertex> runs before that.
  '  vec4 aerialP = vec4(transformed, 1.0);',
  '#ifdef USE_INSTANCING',
  '  aerialP = instanceMatrix * aerialP;',
  '#endif',
  '  vAerialDepth = -(modelViewMatrix * aerialP).z;',
].join('\n');

const AERIAL_FRAG = [
  // Chromaticity from the sky, brightness mostly the fragment's own:
  // the dimming is FogExp2's job and fighting it double-counts.
  `  float aerialL = dot(diffuseColor.rgb, ${LUMA});`,
  '  float aerialT = uAerialK * smoothstep(uAerialStart, uAerialEnd,',
  '      vAerialDepth);',
  // AIRLIGHT, the part pure luminance preservation cannot reach. Air
  // does not only tint what is behind it, it glows: the far field
  // loses contrast from BOTH ends, darks lifting toward the sky and
  // lights settling toward it. Preserving luminance exactly left a
  // shadowed ridge 70 m out as black as one at arm's length — the
  // silhouette that reads as a hole in the frame rather than distance.
  // `uAerialLum` is the sky's own luminance, so at night (a dark sky)
  // this correctly nearly vanishes. ONE-SIDED on purpose: airlight is
  // light ADDED on the way to the eye. Letting it pull the other way
  // dimmed every far surface brighter than the sky, which is the
  // extinction half — three's fog owns that, and doing it twice is how
  // a hazy distance turns into a dark one.
  '  float aerialB = aerialL',
  '      + uAerialLift * max(0.0, uAerialLum - aerialL);',
  '  diffuseColor.rgb = mix(diffuseColor.rgb,',
  '      clamp(uAerialSky * aerialB, 0.0, 1.0), aerialT);',
].join('\n');

const AERIAL_HEAD = [
  'varying float vAerialDepth;',
  'uniform vec3 uAerialSky;',
  'uniform float uAerialStart;',
  'uniform float uAerialEnd;',
  'uniform float uAerialK;',
  'uniform float uAerialLum;',
  'uniform float uAerialLift;',
].join('\n');

/**
 * Desaturate a built-in material toward the sky with distance.
 *
 * FogExp2 dims the far field but never moves its hue, and a saturated
 * far hill under correct fog is what still reads as a painted tabletop.
 * This tints the ALBEDO (at `<color_fragment>`, so lighting, shadows
 * and the fog chunk all still run on top) toward the sky's
 * chromaticity at the fragment's own brightness — it composes with the
 * scene's fog instead of competing with it.
 *
 * @param {THREE.Material} material Any built-in material (the patch
 *   needs three's `<color_fragment>`); patched in place.
 * @param {object} [opts] `scene` THREE.Scene to read — the shift then
 *   targets the scene's OWN fog colour and finishes at one e-fold of
 *   its density, so the two cues cannot disagree about where far is;
 *   `skyColor` THREE.Color override (default the day mood horizon,
 *   matching `worldShell`); `start`/`end` metres over which the shift
 *   ramps in (default 40/600 — `end` is one e-fold of the day fog
 *   density, so the shift completes as fog takes over); `strength`
 *   maximum shift (default 0.8; 1.0 is pure sky); `lift` how much of
 *   the sky's own brightness the far field takes on — airlight
 *   (default 0.35; 0 is the pure hue shift, 1 flattens the far field
 *   to the sky).
 * @returns {THREE.Material} The same material. Apply it LAST: every
 *   `patchStandard` owns `onBeforeCompile`, so a second patch on the
 *   same material replaces this one. Tune later through
 *   `material.userData.uniforms.uAerialK.value`.
 */
export function patchAerialPerspective(material, opts = {}) {
  const env = readScene(opts.scene);
  // The TARGET stays the scene's own fog colour — the whole point is
  // that the hue shift and three's fog agree about what far looks like
  // — but it is floored by the radiance of the air, so a night mood's
  // deliberately black fog cannot take the far field to black with it.
  const sky = opts.skyColor !== undefined ? new THREE.Color(opts.skyColor)
      : inScatterFloor(env.fog || env.air || new THREE.Color(DAY_HORIZON),
                       env.air || env.amb,
                       env.air ? 0.5 * lumOf(env.air) : 0.2 * env.ambLum);
  const start = opts.start ?? 40;
  // One e-fold of the scene's own fog, exactly as the 600 m default is
  // one e-fold of the day mood's 0.0018.
  const efold = env.fogDensity > 0 ? 1 / env.fogDensity : 600;
  const end = Math.max(start + 1, opts.end ?? efold);
  // Pre-divided by its own luminance, so `uAerialSky * L` lands back on
  // the fragment's brightness and the shader stays two instructions.
  const lum = lumOf(sky);
  return patchStandard(material, {
    // One name on purpose: the GLSL is identical for every material, so
    // they SHOULD share a program. The uniforms stay per material.
    name: 'aerialPerspective',
    uniforms: {
      uAerialSky: { value: sky.clone().multiplyScalar(1 / lum) },
      uAerialStart: { value: start },
      uAerialEnd: { value: end },
      uAerialK: { value: opts.strength ?? 0.8 },
      uAerialLum: { value: lum },
      uAerialLift: { value: opts.lift ?? 0.35 },
    },
    vertexHead: 'varying float vAerialDepth;',
    vertexBody: AERIAL_VERT,
    fragmentHead: AERIAL_HEAD,
    fragmentBody: AERIAL_FRAG,
    util: false,
  });
}
