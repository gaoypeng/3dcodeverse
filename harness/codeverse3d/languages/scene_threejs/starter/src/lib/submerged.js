/**
 * Submerged: the water column over what is under it, the rings rain
 * leaves on open water, and a sheet of thin ice over either.
 *
 * A waterline alone leaves a sunk rock as a rock with a line across
 * it. What says UNDER is the column: contrast falls, the colour walks
 * toward the water's own, and red goes first — over DEPTH and over the
 * distance the eye looks through, so a deep floor far off is further
 * gone than the same floor underfoot.
 *
 * The two patches go through `patchStandard`, so each material keeps
 * its lighting, shadows, fog and depth chunks and they CHAIN with the
 * waterline (`waterside.js`), the caustic net (`caustics.js`) and the
 * rock projection (`terrain_shade.js`) the same surface already wears.
 * `makeRainRings` is the open-water half of `rain.js`, whose splashes
 * are for hard ground.
 *
 * NOT a second reflective surface: `water.js` owns the scene's one RTT
 * plane. Everything here shades what is under that plane or lies on it.
 */

import * as THREE from 'three';
import {
  patchStandard, composeRoughness, makeShaderMaterial, instancedQuad,
  keepOutOfDepthPasses,
} from './shader.js';

// Water absorbs red an order of magnitude faster than blue, and that
// spectrum IS the effect. Pure water is 1 : 0.21 : 0.054; lifted a
// little so a 3 m pool does not go pure cyan.
const EXT_RATIO = new THREE.Vector3(1.0, 0.26, 0.10);

// Above the surface it lies on: clears z-fighting with a coplanar
// water plane, and stays inside the 2 cm an asset's base is allowed.
const LIFT = 0.012;

// Named as caustics and waterside name them, so a submerged bank
// wearing a waterline, a net and this declares ONE pair.
const WORLD_VARYINGS = [
  'varying vec3 vAstraWorld;',
  'varying vec3 vAstraWorldN;',
].join('\n');

// One base for both patches. `transformed` is still object-space after
// <begin_vertex>, so the instance transform is folded in by hand or
// every scattered cobble takes its depth from the world origin.
const BASE = {
  name: 'submerged:base',
  vertexHead: WORLD_VARYINGS,
  vertexBody: [
    '  vec4 subWp = vec4(transformed, 1.0);',
    '  vec3 subWn = normal;',
    '#ifdef USE_INSTANCING',
    '  subWp = instanceMatrix * subWp;',
    '  subWn = mat3(instanceMatrix) * subWn;',
    '#endif',
    '  vAstraWorld = (modelMatrix * subWp).xyz;',
    '  vAstraWorldN = normalize((modelMatrix * vec4(subWn, 0.0)).xyz);',
  ].join('\n'),
  fragmentHead: WORLD_VARYINGS,
};

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
function toColor(value, fallback) {
  return new THREE.Color(
      value === undefined || value === null ? fallback : value);
}

/** Clamp to 0..1 without importing MathUtils for one call. */
function unit(value) {
  return Math.max(0, Math.min(1, value));
}

/** Deterministic 0..1 stream: a shipped factory owns its own RNG. */
function rng(seed) {
  let s = (seed >>> 0) || 1;
  return () => ((s = (s * 16807) % 2147483647) / 2147483647);
}

/**
 * A seed becomes a far-apart lattice offset, not a one-cell shift.
 *
 * Seeds 7 and 8 offsetting by 1 would TRANSLATE the field by one cell,
 * which is the same pool twice.
 */
function seedOffset(seed) {
  const s = Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973;
  return new THREE.Vector2(((s * 16807) % 9973) * 0.091,
                           ((s * 48271) % 9973) * 0.067);
}

/**
 * Per-channel extinction: a scalar on water's spectrum, or three
 * coefficients given outright (THREE.Vector3 or [r, g, b], per metre).
 */
function extinctionVec(value) {
  if (value && value.isVector3) return value.clone();
  if (Array.isArray(value)) {
    return new THREE.Vector3(value[0] || 0, value[1] || 0, value[2] || 0);
  }
  const k = value === undefined ? 0.35 : Math.max(0, value);
  return EXT_RATIO.clone().multiplyScalar(k);
}

/**
 * Apply the shared world-space base, then the patch itself.
 *
 * `patchStandard` replaces a patch of the SAME name in place, so the
 * base costs one vertex body however many of these a material wears.
 */
function withBase(material, part) {
  // A raw ShaderMaterial (the addon Water, anything from
  // makeShaderMaterial) has neither hook, so the patch is a silent
  // no-op — the one failure mode nothing else here would report.
  if (material && material.isShaderMaterial) {
    console.warn(
        part.name + ': ' + (material.name || 'material') + ' is a raw ' +
        'ShaderMaterial with no <color_fragment> hook, so this patch ' +
        'does nothing. Patch a standard-material surface instead.');
  }
  patchStandard(material, BASE);
  return patchStandard(material, part);
}

const UNDER_HEAD = [
  'uniform float uSubLevel;',
  'uniform vec3 uSubExt;',
  'uniform vec3 uSubColor;',
  'uniform float uSubMurk;',
  'uniform float uSubLight;',
  'uniform vec2 uSubSeed;',
].join('\n');

const UNDER_BODY = [
  '  float subD = max(uSubLevel - vAstraWorld.y, 0.0);',
  // The eye ray counts only where it runs UNDER the surface: this
  // fragment's share of the climb to the camera, which is 0 at the
  // line however far away the camera is.
  '  float subRise = max(cameraPosition.y - vAstraWorld.y, 1e-4);',
  '  float subWet = clamp(subD / subRise, 0.0, 1.0);',
  '  float subView = length(cameraPosition - vAstraWorld) * subWet;',
  // Silt drifts. A uniform column is glass, and a still one is a
  // stain, so the path length rides a slow seeded field.
  '  vec2 subQ = vAstraWorld.xz * 0.13 + uSubSeed',
  '            + vec2(uTime * 0.011, uTime * -0.008);',
  '  float subS = uSubMurk * (astraFbm2(subQ, 3) - 0.44);',
  '  float subMk = 1.0 + subS;',
  // Turbidity is not a dimmer, it is a HUE. Silt scatters green and
  // drinks blue, so the cloudy reaches of one pool run green where the
  // clear ones run blue — and ONE flat teal over a whole basin is the
  // tell of a tint rather than a column. The swings are bounded: murk
  // caps at 1 and the field at -0.44, so no coefficient can go
  // negative and amplify with depth.
  '  vec3 subK = uSubExt',
  '      * vec3(1.0, 1.0 - 0.30 * subS, 1.0 + 1.60 * subS);',
  '  vec3 subT = exp(-subK * ((subD + subView) * subMk));',
  // Beer-Lambert down and back out: what the surface returns is
  // absorbed along both legs, and red is gone first.
  '  diffuseColor.rgb *= subT;',
  // The column scatters into the ray exactly what the ray lost, lit
  // by the blue that got this deep — so contrast dies into the
  // water's own colour rather than into black. (Filtering the veil per
  // CHANNEL here is the more literal physics and was measured to be
  // worse: the red of a green-blue water colour is already near zero
  // in linear, so the deep end clipped to one channel and came out a
  // flat, hyper-saturated cyan — mean saturation 0.930 -> 0.941 with
  // no visible gain.)
  // ...and by how much light there is to scatter at all. The veil is
  // LIGHT, and no fragment hook can see the scene's lamps — the head
  // is injected above <lights_pars_begin> — so a column left at the
  // daylight number glows the same green through a night scene, which
  // is the one thing a still of a night pool never shows.
  '  vec3 subVeil = uSubColor * uSubLight * (1.0 - subT)',
  '      * exp(-uSubExt.b * subD);',
  // The same silt that swung the path swings the COLOUR: a turbid
  // reach scatters green where a clear one runs blue, so one pool is
  // never one teal. This is where the murk becomes visible — inside
  // the exponent it only moves an already-saturated exponential.
  '  subVeil = astraHueBreak(subVeil, subQ, 1.0, 0.30 + 0.9 * uSubMurk);',
  // A basin is a hundred-pixel ramp of a smooth exponential, which is
  // exactly where 8-bit output lays down contour bands (measured: the
  // longest constant run across the deep water fell from 31 px to 7).
  '  subVeil *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.07;',
  '  totalEmissiveRadiance += subVeil;',
].join('\n');

/**
 * Sink a surface into a water column: contrast out, water colour in.
 *
 * Nothing above `level` is touched — the depth is clamped at the line
 * and the eye ray's underwater share goes to zero there too, so the
 * transmittance is exactly 1 and the veil exactly 0 above it, with no
 * step at the line. Below it the loss runs on the TOTAL path: the
 * depth the light fell through plus the underwater length of the ray
 * back to the camera. That second leg is what makes the far end of a
 * pool floor read as further away rather than merely darker, and it is
 * why a bank photographs bluer across a bay than at your feet.
 *
 * Extinction is per channel, which is the whole point: red is gone in
 * a couple of metres where blue runs for tens, so the albedo walks to
 * the water's colour red-first while the veil the column scatters back
 * fills in what was lost. Contrast between two neighbouring albedos
 * falls by exactly the transmittance, because both take the same veil.
 *
 * Turbidity is a HUE, not a second dimmer. The silt field that swings
 * the path length also swings the SPECTRUM — silt scatters green and
 * drinks blue — and it swings the veil's own colour with it, so the
 * cloudy reaches of one pool run green where the clear ones run blue.
 * One flat teal over a whole basin is the tell of a tint rather than a
 * column, and it costs one field that was already being sampled. The
 * veil then carries a per-pixel dither, because a basin is a hundred
 * pixels of a smooth exponential and that is where 8-bit output lays
 * contours (measured here: the longest constant run across the deep
 * water fell from 31 px to 7).
 *
 * WITH `caustics.js`: they are two halves of one surface and belong on
 * the same material at the same `level`. `patchCaustics` ADDS its net
 * to `totalEmissiveRadiance` tinted by `diffuseColor`; this MULTIPLIES
 * `diffuseColor` by the transmittance and adds the column's veil to
 * that same term. Patches run in CALL ORDER, so call this one FIRST
 * and the net is tinted by the already-extinguished albedo instead of
 * burning white over water that has eaten every other red in frame.
 * The net's own `depthFade` is its extinction: keep it near
 * `1 / extinction` or the net will outlive the floor it lands on. Both
 * share `vAstraWorld`/`vAstraWorldN` by name, so the pair costs one.
 *
 * `level` is a world plane, so EVERYTHING under it is under water,
 * including the dry outside of a pool wall and the far side of a dam.
 * Give those their own material, exactly as `caustics.js` needs.
 *
 * Needs a LIT material — MeshStandard / MeshPhysical: the veil lands
 * on the emissive term, which MeshBasicMaterial does not have. Drive
 * it with `tickShaders(scene, t)`; an un-advanced uTime freezes the
 * silt, which is a stain rather than a column.
 *
 * AT NIGHT, set `light`. The veil is scattered LIGHT and it lands on
 * the emissive term, so nothing in the scene dims it: a column left at
 * the daylight number glows the same green under a night sky, which is
 * the one thing a photograph of a night pool never shows (measured
 * here: the night basin's water sat at mean luminance 0.42 against a
 * 0.25 frame until this was turned down). No fragment hook can read
 * the scene's lamps — patchStandard injects its head ABOVE
 * `<lights_pars_begin>` — so this is the one number that has to be
 * told rather than found.
 *
 * @param {THREE.Material} material A built-in lit material, patched in
 *   place.
 * @param {object} [opts] `level` world Y of the water surface (default
 *   0); `color` THREE.Color or hex the water goes toward (default a
 *   green-blue); `extinction` per-metre loss — a scalar on water's own
 *   spectrum (default 0.35) or a THREE.Vector3 / [r, g, b] of
 *   coefficients outright; `murk` how far drifting silt swings the
 *   path length AND its colour, 0..1 (default 0.5); `light` how much
 *   light there is for the column to scatter, 1 = open daylight
 *   (default 1; 0.15..0.3 under a night sky, more under a lamp);
 *   `seed` moves that silt, so two pools are not one pool twice
 *   (default 1).
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms` so `uSubLevel` can follow a tide.
 */
export function patchUnderwater(material, opts = {}) {
  const level = opts.level === undefined ? 0 : opts.level;
  const murk = opts.murk === undefined ? 0.5 : opts.murk;
  // The one silent failure here: no emissive term means either a raw
  // ShaderMaterial (no hook at all) or a Basic (no such variable).
  if (material && material.emissive === undefined) {
    console.warn(
        'patchUnderwater: ' + (material.name || material.type) + ' has '
        + 'no emissive term — the column scatters LIGHT back into the '
        + 'ray and that lands on totalEmissiveRadiance, which a '
        + 'MeshBasicMaterial and a raw ShaderMaterial do not have. '
        + 'Patch a lit material instead.');
  }
  return withBase(material, {
    name: 'submerged:underwater',
    uniforms: {
      uSubLevel: { value: level },
      uSubExt: { value: extinctionVec(opts.extinction) },
      uSubColor: { value: toColor(opts.color, 0x1a5b63) },
      uSubMurk: { value: unit(murk) },
      // A negative here would subtract light and punch black holes in
      // the water; there is no upper clamp because a lit pool is a
      // real case.
      uSubLight: {
        value: Math.max(0, opts.light === undefined ? 1 : opts.light),
      },
      uSubSeed: { value: seedOffset(opts.seed) },
    },
    fragmentHead: UNDER_HEAD,
    fragmentBody: UNDER_BODY,
  });
}

const RING_VERTEX_HEAD = [
  'attribute vec3 aCorner;',
  'attribute vec3 iOff;',
  'attribute vec3 iExtra;',
  'uniform float uRingRate;',
  'uniform float uRingSize;',
  'uniform float uRingJit;',
].join('\n');

const RING_VERTEX = [
  '  vUv = uv;',
  '  float rgP = iExtra.x + uTime * uRingRate;',
  '  float rgCyc = floor(rgP);',
  '  vAge = fract(rgP);',
  // Each cycle the drop lands somewhere new: a ring that re-forms on
  // the same spot forever is a sprinkler, not rain.
  '  vec2 rgJ = vec2(astraHash21(vec2(iExtra.z, rgCyc)),',
  '                  astraHash21(vec2(rgCyc, iExtra.z + 7.1))) - 0.5;',
  // Every impact catches its own patch of sky: one crest colour over a
  // whole field of rings is the tell of a sheet of decals.
  '  vRgT = astraHash21(vec2(iExtra.z + 2.7, rgCyc)) - 0.5;',
  '  vec3 rgC = iOff + vec3(rgJ.x, 0.0, rgJ.y) * uRingJit;',
  // The quad lies FLAT and keeps its size; the wavefront travels
  // inside it, so the ring is a place on a surface, not a sprite that
  // scales up.
  '  transformed = rgC + vec3(aCorner.x, 0.0, aCorner.y)',
  '      * (uRingSize * iExtra.y);',
].join('\n');

const RING_FRAGMENT_HEAD = [
  'uniform vec3 uRingColor;',
  'uniform vec3 uRingTrough;',
  'uniform float uRingWidth;',
  'uniform float uRingOpacity;',
].join('\n');

const RING_FRAGMENT = [
  '  float rgR = length(vUv * 2.0 - 1.0);',
  '  if (rgR > 1.0) discard;',
  // Where the wave IS, not how faded a sprite is: the front runs out
  // and DECELERATES, so a still frame catches rings bunched near the
  // rim and wide apart near the impact.
  '  float rgFront = 0.90 * pow(vAge, 0.62);',
  '  float rgAA = max(fwidth(rgR), 0.002);',
  '  float rgW = max(uRingWidth * (1.0 + rgFront), rgAA * 1.6);',
  '  float rgA = (rgR - rgFront) / rgW;',
  '  float rgCrest = exp(-rgA * rgA);',
  // The capillary train behind the front, and the trough between the
  // two. A lone bright circle is a decal; a crest with a dark
  // shoulder is a wave on a surface.
  '  float rgB = (rgR - rgFront * 0.62) / (rgW * 1.7);',
  '  float rgTrain = 0.40 * exp(-rgB * rgB);',
  '  float rgD = (rgR - rgFront * 0.84) / (rgW * 1.5);',
  '  float rgDip = 0.30 * exp(-rgD * rgD);',
  // Amplitude falls as the same water spreads over a longer front,
  // then the whole ring dies with age; born soft so it cannot pop.
  '  float rgFade = (1.0 - vAge) * smoothstep(0.0, 0.07, vAge)',
  '      / (1.0 + 2.2 * rgFront);',
  '  rgFade *= 1.0 - smoothstep(0.86, 1.0, rgR);',
  // A ring narrower than a couple of pixels can only alias into a
  // hard little square, so it does not draw until it has spread.
  '  rgFade *= smoothstep(rgAA * 1.2, rgAA * 3.5, rgFront);',
  '  float rgUp = rgCrest + rgTrain;',
  '  float a = (rgUp + rgDip) * rgFade * uRingOpacity;',
  '  if (a < 0.004) discard;',
  '  vec3 rgCol = mix(uRingTrough, uRingColor,',
  '                   rgUp / max(rgUp + rgDip, 1e-4));',
  // Warm one drop, cool the next, about the crest colour the caller
  // gave: a field of identical rings reads as printed, and the swing
  // is per IMPACT rather than per pixel so a single ring stays one
  // wave rather than turning speckled.
  '  rgCol *= 1.0 + vRgT * vec3(0.34, 0.14, -0.24);',
  '  gl_FragColor = vec4(rgCol, clamp(a, 0.0, 1.0));',
].join('\n');

/**
 * The expanding rings rain makes on open water.
 *
 * `rain.js`'s `makeSplashes` is the hard-ground half of this — crowns
 * of spray thrown up off a road. On water there is no crown to speak
 * of: what you see is a ring of surface travelling out from the
 * impact, and the three things that make it read as a surface rather
 * than a sprite are all here. The quad is FLAT and a fixed size, so
 * the wavefront moves across a patch of water instead of the whole
 * card scaling up. The front DECELERATES and its amplitude falls as it
 * spreads, so a frozen frame shows rings bunched and faint at the rim
 * and wide and sharp near the drop. And each ring is a crest, a
 * trailing capillary crest and a DARK trough between them, so it
 * bends the water rather than painting a white circle on it.
 *
 * Impacts relocate every cycle, hashed on the cycle index, so the
 * field is rain and not a fixed row of drippers. Add the mesh at the
 * scene ROOT (it is world-space, like `rain.js`) and drive it from
 * `tick`: `obj.userData.update(t)`, or one `tickShaders(scene, t)`.
 *
 * It lies `0.012` above `y` to clear a coplanar water plane, and it is
 * kept out of the depth passes: a transparent card is a solid wall to
 * the ambient-occlusion override material, and this is a field of them.
 *
 * @param {object} [opts]
 *   `area` (default `{x: 0, z: 0, w: 24, d: 24}`, or a number for a
 *     square) the water rect the rings land on;
 *   `rate` (default 1.6) impacts per second per site;
 *   `size` (default 0.55) metres across a fully spread ring;
 *   `y` (default 0) world Y of the water surface;
 *   `seed` (default 7) placement and phase seed;
 *   `count` (default 320) impact sites — one draw call regardless;
 *   `color` (default 0xdfeaf0) the crest, which catches the sky;
 *   `trough` (default 0x2a3a44) the dark shoulder between the crests;
 *   `width` (default 0.055) crest thickness as a fraction of `size`;
 *   `opacity` (default 0.42) peak alpha.
 * @returns {THREE.Mesh} Mesh named 'RainRings' with
 *   `userData.update(t)` advancing the rings.
 */
export function makeRainRings(opts = {}) {
  // A number is truthy, so `area: 12` would read w/d as undefined and
  // put every ring at NaN — measured in rain.js, same trap.
  const area = typeof opts.area === 'number'
      ? { x: 0, z: 0, w: opts.area, d: opts.area }
      : (opts.area || { x: 0, z: 0, w: 24, d: 24 });
  const rate = opts.rate === undefined ? 1.6 : opts.rate;
  const size = opts.size === undefined ? 0.55 : opts.size;
  const y = opts.y === undefined ? 0 : opts.y;
  const count = Math.max(1, opts.count === undefined ? 320 : opts.count);
  const width = opts.width === undefined ? 0.055 : opts.width;
  const opacity = opts.opacity === undefined ? 0.42 : opts.opacity;
  const rand = rng(opts.seed === undefined ? 7 : opts.seed);

  const off = new Float32Array(count * 3);
  const ext = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    off[i * 3] = area.x + (rand() - 0.5) * area.w;
    off[i * 3 + 1] = y + LIFT;
    off[i * 3 + 2] = area.z + (rand() - 0.5) * area.d;
    ext[i * 3] = rand();               // phase: a still shows all radii
    ext[i * 3 + 1] = 0.7 + rand() * 0.6;  // size of this drop's ring
    ext[i * 3 + 2] = rand() * 97.0;    // identity, for the relocation
  }
  // Culling reads the geometry's SPHERE and `position` is all zeros,
  // so a real radius here is what stops three dropping the whole field
  // the moment the origin leaves frame.
  const reach = Math.hypot(Math.abs(area.x) + area.w * 0.5 + size,
                           Math.abs(area.z) + area.d * 0.5 + size);
  const geo = instancedQuad(count, 1, 1,
                            Math.hypot(reach, Math.abs(y) + size));
  geo.setAttribute('iOff', new THREE.InstancedBufferAttribute(off, 3));
  geo.setAttribute('iExtra', new THREE.InstancedBufferAttribute(ext, 3));

  const mat = makeShaderMaterial({
    name: 'RainRings',
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      uRingRate: { value: rate },
      uRingSize: { value: Math.max(size, 1e-3) },
      // Relocate inside this site's own share of the rect, so the
      // drops cover the water instead of clumping.
      uRingJit: {
        value: Math.sqrt(Math.max(area.w * area.d, 0) / count),
      },
      uRingColor: { value: toColor(opts.color, 0xdfeaf0) },
      uRingTrough: { value: toColor(opts.trough, 0x2a3a44) },
      uRingWidth: { value: Math.max(width, 1e-3) },
      uRingOpacity: { value: unit(opacity) },
    },
    varyings: 'varying vec2 vUv;\nvarying float vAge;\n'
        + 'varying float vRgT;',
    vertexHead: RING_VERTEX_HEAD,
    vertexMain: RING_VERTEX,
    fragmentHead: RING_FRAGMENT_HEAD,
    fragmentMain: RING_FRAGMENT,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'RainRings';
  mesh.renderOrder = 2;
  mesh.userData.update = (t) => { mat.uniforms.uTime.value = t; };
  return keepOutOfDepthPasses(mesh);
}

const ICE_VARYING = 'varying vec2 vAstraIceUv;';

const ICE_HEAD = [
  'uniform float uIceThick;',
  'uniform float uIceDens;',
  'uniform float uIceFrost;',
  'uniform float uIceRim;',
  'uniform vec3 uIceColor;',
  'uniform vec2 uIceSeed;',
  // Arc length to the nearest of `n` rays from the cell's origin, and
  // WHICH ray (mod n, so an arm survives atan's seam). Scaling the
  // phase by n makes 2n keep every ray of n and add one between.
  'vec2 astraIceRay(vec2 d, float n, float j) {',
  '  float x = (atan(d.y, d.x) * 0.15915494 + j) * n;',
  '  float k = floor(x + 0.5);',
  '  float g = abs(x - k) / n * 6.2831853 * max(length(d), 0.18);',
  '  return vec2(g, mod(k, n));',
  '}',
  // A crack BRANCHES: the ray count doubles every step out and the two
  // levels cross-fade, so an old crack runs on while a new one grows
  // from it. Each arm runs its OWN length, or the fan is a snowflake.
  'float astraIceCracks(vec2 p, float w) {',
  '  vec2 id = floor(p);',
  '  vec2 o = vec2(astraHash21(id), astraHash21(id + 19.7)) - 0.5;',
  '  vec2 d = fract(p) - 0.5 - o * 0.6 + 1e-4;',
  '  float r = length(d);',
  '  float lv = floor(r * 3.0);',
  '  float j = astraHash21(id + 3.1);',
  '  float n = 3.0 * exp2(lv);',
  '  vec2 ra = astraIceRay(d, n, j);',
  '  vec2 rb = astraIceRay(d, n * 2.0, j);',
  '  float f = fract(r * 3.0);',
  '  float g = mix(ra.x, rb.x, f);',
  '  float reach = mix(astraHash21(id + ra.y * 0.37 + 5.0),',
  '                    astraHash21(id + rb.y * 0.37 + 5.0), f);',
  '  reach = 0.30 + 0.55 * reach;',
  '  return (1.0 - smoothstep(w * 0.35, w, g))',
  '      * (1.0 - smoothstep(reach * 0.55, reach, r));',
  '}',
].join('\n');

const ICE_BODY = [
  // Refract at the surface and run the ray down by the thickness: the
  // interior then SLIDES against the surface as the eye moves, which
  // is the parallax that reads as looking through something.
  '  vec3 iceV = normalize(vAstraWorld - cameraPosition);',
  '  vec3 iceN = normalize(vAstraWorldN);',
  '  iceN = faceforward(iceN, iceV, iceN);',
  // Air into ice is eta < 1, so refract() can never return the zero
  // vector here and there is no total-internal-reflection case.
  '  vec3 iceRf = refract(iceV, iceN, 0.763);',
  '  vec2 icePar = vAstraWorld.xz',
  '      + iceRf.xz * (uIceThick / max(abs(iceRf.y), 0.25));',
  // Thickness is a column like the water's: what is under the sheet
  // dims and takes the ice's colour the further down it sits. Ice is
  // CLEAR — a hand's width of it is nearly nothing — so the
  // coefficient is 1.6 per metre. At the 3.0 this shipped with, a
  // 10 cm sheet mixed a quarter of a pale mint over the dark water and
  // landed at an albedo of 0.25 linear, which on a sunlit horizontal
  // plane is 200/255 before a single crack is drawn: a milk plate.
  '  float iceK = 1.0 - exp(-uIceThick * 1.6);',
  '  vec3 iceC = mix(diffuseColor.rgb, uIceColor, iceK);',
  '  float iceB = astraFbm2(icePar * 2.6 + uIceSeed, 3);',
  '  iceC *= 0.78 + 0.52 * iceB;',
  '  iceC = mix(iceC, uIceColor + 0.22,',
  '             smoothstep(0.52, 0.86, iceB) * iceK * 0.7);',
  // Frozen-in air is not one colour: the bubble clouds scatter warm
  // where the light reaches them and the clear ice between runs blue.
  '  iceC = astraHueBreak(iceC, icePar * 1.4, 1.0, 0.30);',
  // FRESNEL. Everything above came from INSIDE the sheet, and only
  // what the surface let in ever got there — at a grazing angle almost
  // nothing does, which is why a frozen pond across a bay is a sheet
  // of sky and its cracks appear as you walk out onto it. The renderer
  // already adds that reflection as specular, so without this term the
  // sheet is paid for the sky twice and lands on a white card
  // (measured here: 200/255 flat at an 11 deg view, cracks invisible).
  '  float iceFz = astraFresnel(iceN, -iceV, 5.0);',
  '  iceC *= 1.0 - 0.78 * iceFz;',
  // The cracks live on the SURFACE, so they take the unrefracted
  // point and they are NOT behind that fresnel: crack over bubble is
  // what fixes the two at their depths.
  '  vec2 iceP = vAstraWorld.xz * uIceDens + uIceSeed;',
  '  iceP += (astraNoise2(iceP * 0.9 + 5.7) - 0.5) * 0.35;',
  // A crack is 2 cm of ice wherever it is, so its width is stated in
  // METRES and converted by the density — a width in cell units would
  // be 5 cm on a dense sheet and 20 cm on a sparse one.
  '  float iceW = max(uIceDens * 0.022,',
  '      max(fwidth(iceP.x), fwidth(iceP.y)) * 1.3);',
  // The second layer is COARSER and turned: a few long masters over
  // the finer network, where a finer second layer is only speckle.
  '  vec2 iceQ = vec2(iceP.x * 0.6 - iceP.y * 0.8,',
  '                   iceP.x * 0.8 + iceP.y * 0.6) * 0.55 + 31.7;',
  '  float iceCr = max(astraIceCracks(iceP, iceW),',
  '                    astraIceCracks(iceQ, iceW * 0.55));',
  // A fissure is TWO things at once: the fracture planes scatter white
  // and the gap between them is a slot of the dark water below. Drawn
  // as one bright line it is a chalk mark on a plate, so the core of
  // the same field — free, it is already computed — darkens back.
  '  float iceCore = smoothstep(0.55, 0.98, iceCr);',
  '  iceC = mix(iceC, vec3(0.87, 0.93, 0.96) * (0.84 + 0.30 * iceB),',
  '             clamp(iceCr, 0.0, 1.0) * 0.80);',
  '  iceC *= 1.0 - 0.55 * iceCore;',
  // The rim is the sheet's own UV border — cut the sheet to the bank
  // and that border IS the bank — torn by noise, because a bank does
  // not rule a straight line.
  '  float iceE = min(min(vAstraIceUv.x, 1.0 - vAstraIceUv.x),',
  '                   min(vAstraIceUv.y, 1.0 - vAstraIceUv.y));',
  '  iceE += (astraFbm2(vAstraWorld.xz * 1.7 + uIceSeed, 3) - 0.44)',
  '      * uIceRim * 0.9;',
  '  float iceFr = astraContact(iceE, uIceRim) * uIceFrost;',
  '  float iceFn = astraFbm2(vAstraWorld.xz * 9.0 + uIceSeed, 3);',
  '  iceFr *= 0.45 + 0.75 * iceFn;',
  // Frost is crystal, not paint. It sits ABOVE the surface, so it does
  // not pay the fresnel either, and it is never one white: each patch
  // takes its own light and its own warm/cool cast.
  '  vec3 iceFc = astraHueBreak(',
  '      vec3(0.90, 0.94, 0.97) * (0.84 + 0.28 * iceFn),',
  '      vAstraWorld.xz, 0.8, 0.22);',
  '  iceC = mix(iceC, iceFc, clamp(iceFr, 0.0, 1.0));',
  // A sheet is a wide smooth ramp of thickness, bubble and frost, and
  // a smooth ramp is where 8-bit output lays down contour bands.
  '  iceC *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.03;',
  '  diffuseColor.rgb = clamp(iceC, 0.0, 1.0);',
].join('\n');

/**
 * A sheet of thin ice over water or stone: depth, cracks, frosted rim.
 *
 * Patch the SHEET, and give it the colour of what is UNDER it — the
 * dark water or the stone — because that is what the shader takes as
 * the thing seen through the ice. Three layers at three depths are
 * what makes it a slab rather than a white plane. The interior is
 * sampled at the point the eye ray reaches after REFRACTING at the
 * surface and running down by `thickness`, so bubbles and murk slide
 * against the surface as the camera moves and the sheet acquires a
 * near and a far face. The cracks are sampled unrefracted, so they sit
 * ON the surface above that interior. The frost sits on top of both.
 *
 * The interior is behind a FRESNEL and the two surface layers are not.
 * Only what the air-ice interface let in ever reached the bubbles, so
 * at a grazing angle the interior all but vanishes and what remains is
 * the sky the renderer reflects off the sheet as specular: a frozen
 * pond across a bay IS a sheet of sky, and its cracks appear as you
 * walk out onto it. Without that term the sheet is paid for the sky
 * twice and every grazing angle lands on a white card.
 *
 * The cracks radiate and BRANCH: rays leave a jittered origin per
 * cell, and the ray count doubles every step outward with the phase
 * scaled to match, which keeps every existing ray and grows a new one
 * between them. That is a fork; one noise call is a smear, and it is
 * the difference between ice and cracked paint. Two rotated layers are
 * maxed together so the cell grid does not read as a grid.
 *
 * The frosted rim is found from the sheet's own UV border, wandered by
 * noise: cut the sheet to fit the bank and that border IS the bank.
 * A geometry with no uv has no border, and frosts everywhere — give
 * the sheet a PlaneGeometry's uv or pass `frost: 0`.
 *
 * Gloss goes through `composeRoughness`, the only route to it: the one
 * fragment hook runs before `<roughnessmap_fragment>`, so roughness is
 * a material value, and frostier ice is duller.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place.
 * @param {object} [opts] `thickness` metres of ice, driving the
 *   parallax and the tint (default 0.08); `crackDensity` crack cells
 *   per metre (default 0.7 — an origin every metre and a half);
 *   `frost` how heavy the rim, 0..1 (default 0.8); `rim` width of that
 *   band in UV, so 0.12 is a tenth of the sheet (default 0.12);
 *   `color` THREE.Color or hex of the ice itself (default a cold
 *   green-white); `seed` moves cracks and bubbles (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`.
 */
export function patchThinIce(material, opts = {}) {
  const thick = opts.thickness === undefined ? 0.08 : opts.thickness;
  const dens = opts.crackDensity === undefined ? 0.7
                                               : opts.crackDensity;
  const frost = opts.frost === undefined ? 0.8 : opts.frost;
  const rim = opts.rim === undefined ? 0.12 : opts.rim;
  // Ice is glossy and frost is not, so the sheet's roughness is the
  // one thing here that cannot be a uniform.
  composeRoughness(material, 'submerged:thinIce',
                   0.18 + 0.62 * unit(frost));
  return withBase(material, {
    name: 'submerged:thinIce',
    uniforms: {
      uIceThick: { value: Math.max(thick, 1e-3) },
      uIceDens: { value: Math.max(dens, 1e-3) },
      uIceFrost: { value: unit(frost) },
      uIceRim: { value: Math.max(rim, 1e-3) },
      uIceColor: { value: toColor(opts.color, 0x9fc4c6) },
      uIceSeed: { value: seedOffset(opts.seed) },
    },
    vertexHead: ICE_VARYING,
    vertexBody: '  vAstraIceUv = uv;',
    fragmentHead: [ICE_VARYING, ICE_HEAD].join('\n'),
    fragmentBody: ICE_BODY,
  });
}
