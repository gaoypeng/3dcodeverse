/**
 * Caustics: the net of light a water surface throws on what is under it.
 *
 * The one cue that says a floor is UNDERWATER rather than merely blue,
 * and it belongs to the lit surface, not to the water — a pool floor,
 * harbour steps, a boulder, a swimmer. It is a `patchStandard`, so the
 * material keeps its lighting, shadows, fog and depth chunks and can
 * chain with the waterline (`waterside.js`), the rock projection
 * (`terrain_shade.js`) and the wear (`surface_wear.js`) it will
 * normally be wearing already.
 */

import * as THREE from 'three';
import { patchStandard } from './shader.js';

// Named as terrain_shade and waterside name them, so a submerged bank
// wearing a splat, a waterline and this declares ONE pair.
const WORLD_VARYINGS = [
  'varying vec3 vAstraWorld;',
  'varying vec3 vAstraWorldN;',
].join('\n');

// `transformed` is still object-space after <begin_vertex>, so an
// instanced boulder takes the origin's depth unless this is folded in.
const CAU_VERTEX = [
  '  vec4 cauWp = vec4(transformed, 1.0);',
  '  vec3 cauWn = normal;',
  '#ifdef USE_INSTANCING',
  '  cauWp = instanceMatrix * cauWp;',
  '  cauWn = mat3(instanceMatrix) * cauWn;',
  '#endif',
  '  vAstraWorld = (modelMatrix * cauWp).xyz;',
  '  vAstraWorldN = normalize((modelMatrix * vec4(cauWn, 0.0)).xyz);',
].join('\n');

const CAU_HEAD = [
  'uniform float uCauLevel;',
  'uniform vec3 uCauSun;',
  'uniform vec2 uCauSlide;',
  'uniform float uCauScale;',
  'uniform float uCauAmt;',
  'uniform float uCauSpeed;',
  'uniform vec3 uCauColor;',
  'uniform float uCauFade;',
  'uniform vec3 uCauAbs;',
  // xyz = a LOCAL source over the water, w = 1 / its reach. w = 0 (the
  // default) is the sun: parallel light, no falloff, no branch.
  'uniform vec4 uCauSrc;',
  'uniform vec2 uCauSeed;',
  // A crease, not a blob: folded about its own mid-line, noise leaves a
  // ridge along every contour, and the power narrows that ridge to a
  // filament. The second octave kinks it; one alone draws smooth worms.
  'float astraCauWeb(vec2 p) {',
  // fbm2 over two octaves tops out at 0.75, so 2.667 centres the fold.
  '  float d = abs(astraFbm2(p, 2) * 2.667 - 1.0);',
  '  return pow(1.0 - d, 16.0);',
  '}',
].join('\n');

const CAU_BODY = [
  '  float cauD = max(uCauLevel - vAstraWorld.y, 0.0);',
  // Shade from where this fragment's light ENTERED the water: the net
  // belongs to the surface overhead, so a riser takes a stretched
  // slice of the floor's net instead of a pattern of its own.
  '  vec2 cauP = (vAstraWorld.xz + uCauSlide * cauD) / uCauScale',
  '            + uCauSeed;',
  '  float cauAa = max(fwidth(cauP.x), fwidth(cauP.y));',
  '  float cauT = uTime * uCauSpeed;',
  // Water does not slide past itself: warping the sample point is what
  // makes the net writhe where a scrolled one would crawl.
  '  vec2 cauW = vec2(astraNoise2(cauP * 0.35 + cauT * 0.07),',
  '                   astraNoise2(cauP * 0.35 - cauT * 0.06 + 7.3));',
  '  cauP += (cauW - 0.5) * 0.40;',
  // And a second, finer, faster warp: without it the filaments are
  // clean arcs, which is the one thing water never draws.
  '  cauP += (vec2(astraNoise2(cauP * 2.2 + cauT * 0.30),',
  '                astraNoise2(cauP * 2.2 - cauT * 0.27 + 3.7)) - 0.5)',
  '          * 0.10;',
  // Two wave trains crossing near a right angle. Each is sampled in
  // its OWN frame, stretched across its travel, because a train's
  // crests — and so its filaments — run across the way it runs.
  '  vec2 cauU = vec2(0.86, 0.51);',
  '  vec2 cauV = vec2(-0.51, 0.86);',
  '  float cauA = astraCauWeb(vec2(dot(cauP, cauU) + cauT,',
  '                                dot(cauP, cauV) * 0.80));',
  '  float cauB = astraCauWeb(vec2(dot(cauP, cauV) * 1.43 - cauT * 0.83,',
  '                                dot(cauP, cauU) * 1.14 + 3.1));',
  // Their sum is the net; their product flares the crossings into the
  // bright nodes, which travel at neither train's speed. That
  // interference is the caustic — one field of it is a smear. The two
  // halves are kept APART because they are not the same light: a node
  // is where every wavelength converged, a filament is one fold's
  // edge, and they are coloured differently below.
  '  float cauFil = (cauA + cauB) * 0.5;',
  '  float cauNode = cauA * cauB;',
  '  float cauNet = cauFil + 4.0 * cauNode;',
  // A third, slow, broad cell: open water is not uniformly lit, and
  // this is where the swell has bunched the light.
  '  float cauC = astraNoise2(cauP * 0.30',
  '      + vec2(0.20, -0.14) * (cauT * 0.21) + 11.7);',
  '  float cauSwell = 0.35 + 1.30 * cauC;',
  '  cauNet *= cauSwell;',
  // Once a pixel spans a cell the net can only alias into sparkle —
  // but fading it to BLACK draws a line across the floor where the
  // water stops being lit. It fades to the field's own mean instead,
  // keeping the swell, so distance goes smooth and stays lit.
  '  cauNet = mix(cauNet, 0.30 * cauSwell,',
  '               smoothstep(0.25, 0.9, cauAa));',
  // Two folds plus their product run to 5, and the swell carries that
  // to 8: 8 x strength is far outside anything a tone map holds, so
  // the brightest nodes came out of ACES as the same flat white as the
  // ones a third as bright — the net lost its own structure at the top
  // and took the floor's material with it. This rolls the peaks toward
  // 5.5 instead, which lands a node near 3.5 units of radiance: inside
  // the tone map's shoulder, and inside a bloom threshold's range.
  '  cauNet = cauNet / (1.0 + 0.18 * cauNet);',
  // Light needs a run to converge: nothing at the line, a focus a
  // fraction of a depth down. The column it loses light IN is the
  // tint below — extinction is per channel, not one number.
  '  float cauK = smoothstep(0.0, uCauFade * 0.3, cauD);',
  '  cauK *= 0.12 + 0.88 * max(dot(normalize(vAstraWorldN), uCauSun),',
  '                            0.0);',
  // A long shallow shelf is one smooth ramp of this term over hundreds
  // of pixels, which is exactly where 8-bit output bands.
  '  cauK *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.05;',
  // A lamp over a pool at night lights a POOL of net, not a reservoir:
  // inverse-square in units of its reach, and identically 1.0 for the
  // sun, whose w is zero.
  '  float cauR = length(vAstraWorld.xz - uCauSrc.xz) * uCauSrc.w;',
  '  cauK /= 1.0 + cauR * cauR;',
  // WATER DRINKS RED FIRST. The scalar fade this replaces dimmed the
  // net evenly and left a deep floor lit the same colour as a shin-
  // deep one; a per-channel Beer-Lambert is what makes shallow water
  // warm and a depth go green-blue — the whole reason a pool
  // photographs. uCauAbs is luminance-neutral, so the net still dims
  // over exactly the `depthFade` metres asked for: only the hue splits.
  '  vec3 cauTint = uCauColor * exp(-cauD * uCauAbs);',
  // Dispersion: a lone filament is a fold seen edge-on and keeps the
  // cool end, a crossing is achromatic and runs warm-white.
  '  float cauNf = cauNode / (cauNode + cauFil * 0.5 + 1e-4);',
  '  cauTint *= mix(vec3(0.84, 0.98, 1.10), vec3(1.08, 1.00, 0.92),',
  '                 cauNf);',
  // And broken colour over metres, so no two bays of one pool are the
  // same green — a single flat tint is the tell of a painted pattern.
  '  cauTint = astraHueBreak(cauTint, cauP, 0.25, 0.22);',
  // Caustics are LIGHT, so they land on the emissive term; tinted by
  // the albedo, because what the eye sees is that light reflected.
  '  totalEmissiveRadiance += cauTint * diffuseColor.rgb',
  '      * (cauNet * cauK * uCauAmt);',
].join('\n');

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
function toColor(value, fallback) {
  return new THREE.Color(
      value === undefined || value === null ? fallback : value);
}

/**
 * Metres a sunbeam slides sideways per metre of depth, REFRACTED.
 *
 * Water bends the ray toward the vertical (n = 1.333), so the air
 * tangent overshoots — at a low sun by more than a third, which would
 * shear the net right off the wall it is supposed to be climbing.
 */
function refractSlide(sun) {
  const sinAir = Math.min(Math.hypot(sun.x, sun.z), 1);
  const sinWater = Math.min(sinAir / 1.333, 0.999);
  const tanWater = sinWater / Math.sqrt(1 - sinWater * sinWater);
  return new THREE.Vector2(sun.x, sun.z).normalize()
      .multiplyScalar(tanWater);
}

/**
 * Per-channel extinction of the beam through the water column.
 *
 * Water is not a grey filter: red is gone in a couple of metres where
 * blue crosses tens, which is why every photograph of a pool ramps warm
 * at the step and green-blue at the drain. Red runs 0.62 of the
 * caller's `depthFade` and blue 1.45 — pulled IN from the ~20:1 clear
 * water really has, because the true ratio takes the net monochrome
 * cyan inside three metres and a pool is not an ocean trench.
 *
 * NORMALISED to luminance, so this is a pure hue split and not a
 * dimmer: mid-grey still decays over exactly the metres the caller
 * asked for, and `depthFade` keeps meaning what it meant before there
 * was a colour here at all. (Weighting by Rec.709 rather than thirds
 * matters — green carries 71% of the luminance and is the channel in
 * the middle, so an unweighted normalisation would darken the net by a
 * tenth at every depth.)
 */
function absorption(fade) {
  const w = new THREE.Vector3(1 / 0.62, 1 / 1.00, 1 / 1.45);
  const lum = 0.2126 * w.x + 0.7152 * w.y + 0.0722 * w.z;
  return w.divideScalar(lum * Math.max(1e-3, fade));
}

/**
 * A local source over the water, packed as (x, y, z, 1 / reach).
 *
 * The sun is parallel light and covers a bay as evenly as a puddle, so
 * it is the default and its `w` is 0 — which makes the falloff term in
 * the shader exactly 1.0 with no branch. A LAMP is the opposite: a
 * night pool is a pool, and one bulb lighting a whole reservoir's floor
 * is the tell that the net is a texture. `reach` is where the net is
 * down to half.
 */
function sourceReach(source, reach) {
  if (!source) return new THREE.Vector4(0, 0, 0, 0);
  const r = Math.max(1e-3, reach === undefined ? 8 : reach);
  return new THREE.Vector4(source.x, source.y, source.z, 1 / r);
}

/**
 * A seed becomes a far-apart lattice offset, not a one-cell shift.
 *
 * Seeds 7 and 8 offsetting by 1 would TRANSLATE the net by one cell,
 * which is the same pool twice.
 */
function seedOffset(seed) {
  const s = Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973;
  return new THREE.Vector2(((s * 16807) % 9973) * 0.103,
                           ((s * 48271) % 9973) * 0.071);
}

/**
 * Throw a moving net of caustic light on what is under the water.
 *
 * Applied to the material of the surface being LIT, never to the
 * water. Nothing above `level` is touched at all; below it the net
 * fades IN over the first fraction of a depth — light needs a run to
 * converge — and out again with the water column, which is what
 * separates a lit pool from a floor with a pattern painted on it.
 *
 * Two things earn the look. The pattern is sampled where the sunbeam
 * ENTERED the water: this fragment's world XZ slid toward the sun by
 * its own depth, refracted. So the net belongs to the surface overhead
 * and a step riser takes a stretched slice of the same net rather than
 * a pattern of its own. And it is ridged interference — two wave
 * trains crossing, plus a slow third that bunches the light — so the
 * bright nodes travel at neither train's speed, where one fbm at any
 * speed reads as dirt sliding over the floor.
 *
 * The third is the COLOUR. The light reaching a fragment has crossed
 * its own depth of water, and water drinks red long before blue, so the
 * tint is a per-channel Beer-Lambert rather than one fade: warm at the
 * step, green-blue at the drain, all from one `depthFade` (measured on
 * a two-slab rig, blue/red 1.17 -> 1.41 at 2.4 m against 0.95 -> 0.92
 * at 0.4 m). Over that, the crossings run warm-white — every wavelength
 * converged there — against cooler filaments, and a slow broken-colour
 * field keeps two bays of one pool from being the same green. And the
 * whole net is rolled off before it is scaled, so a bright node lands
 * near 3.5 units of radiance rather than the 30 that came out of the
 * tone map as flat white with the floor's material inside it.
 *
 * It lifts `totalEmissiveRadiance`, tinted by `diffuseColor`, and does
 * NOT change albedo: caustics are light, and albedo alone would cap
 * them at the light the surface already gets, so a shaded corner or a
 * soffit would take none. That needs a LIT material — MeshStandard /
 * MeshPhysical, not Basic, which has no emissive term at all. Drive it
 * with `tickShaders(scene, t)`; an un-advanced uTime is a frozen net.
 *
 * @param {THREE.Material} material A built-in lit material, patched in
 *   place.
 * @param {object} [opts] `level` world Y of the water surface (default
 *   0); `sunDir` THREE.Vector3 TOWARD the sun — the same one the key
 *   light uses (default 0.45, 0.75, 0.35); `scale` metres across one
 *   cell of the net (default 0.6); `strength` how hard the net
 *   burns — a node peaks near 3 x this (default 4); `speed` how fast
 *   the water runs (default 0.35); `color` THREE.Color or hex of the
 *   light ABOVE the surface, before the water tints it (default a
 *   warm daylight white); `depthFade` metres of water the net survives
 *   in luminance — red keeps 0.62 of that and blue 1.45, which is the
 *   ramp from a warm step to a green-blue drain (default 3); `seed`
 *   moves the lattice, so two pools are not one pool twice (default 1);
 *   `source` a THREE.Vector3 world position when the light over the
 *   water is a LAMP rather than the sun, with `reach` the metres at
 *   which its net is half gone (default 8) — omit both for sunlight.
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms` so `uCauLevel` can follow a tide.
 */
export function patchCaustics(material, opts = {}) {
  const level = opts.level === undefined ? 0 : opts.level;
  const scale = opts.scale === undefined ? 0.6 : opts.scale;
  const strength = opts.strength === undefined ? 4 : opts.strength;
  const speed = opts.speed === undefined ? 0.35 : opts.speed;
  const fade = opts.depthFade === undefined ? 3 : opts.depthFade;
  const sun = (opts.sunDir ? opts.sunDir.clone()
      : new THREE.Vector3(0.45, 0.75, 0.35)).normalize();
  const slide = refractSlide(sun);
  // The one silent failure here: no emissive term means either a raw
  // ShaderMaterial (no hook at all) or a Basic (no such variable).
  if (material && material.emissive === undefined) {
    console.warn(
        'patchCaustics: ' + (material.name || material.type) + ' has no '
        + 'emissive term — caustics are LIGHT and land on '
        + 'totalEmissiveRadiance, which a MeshBasicMaterial and a raw '
        + 'ShaderMaterial do not have. Patch a lit material instead.');
  }
  return patchStandard(material, {
    name: 'caustics:net',
    uniforms: {
      uCauLevel: { value: level },
      uCauSun: { value: sun },
      uCauSlide: { value: slide },
      uCauScale: { value: Math.max(1e-3, scale) },
      uCauAmt: { value: Math.max(0, strength) },
      uCauSpeed: { value: speed },
      uCauColor: { value: toColor(opts.color, 0xfff2e2) },
      uCauFade: { value: Math.max(1e-3, fade) },
      uCauAbs: { value: absorption(fade) },
      uCauSrc: { value: sourceReach(opts.source, opts.reach) },
      uCauSeed: { value: seedOffset(opts.seed) },
    },
    vertexHead: WORLD_VARYINGS,
    vertexBody: CAU_VERTEX,
    fragmentHead: [WORLD_VARYINGS, CAU_HEAD].join('\n'),
    fragmentBody: CAU_BODY,
  });
}
