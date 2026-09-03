/**
 * The two marks that make a cliff read as ROCK and not as brown
 * geometry: the beds it was laid down in, and the runs the rain has cut
 * down it. Neither is decoration — a face with no bedding reads as a
 * displaced blob, and one with no runs reads as a clean model of rock.
 *
 * Both go through `patchStandard`, so the built-in's lighting, shadows,
 * fog and depth chunks survive, and both shade from world position and
 * world normal — no UV to author, and a face at any angle gets the same
 * geology. They are meant to land ON TOP of `patchTriplanar` /
 * `patchSlopeSplat`, whose noise keeps showing through both.
 *
 * The constraint that shapes them: a fragment sees its own position,
 * its own normal and its own screen derivatives, and nothing else. So
 * the beds ride world ALTITUDE — one stack for the whole scene, which
 * is why two cliffs a hundred metres apart line up — and a run learns
 * where water collects only from the way the surface BENDS under it (a
 * gully across the flow, a lip along it) plus its own field smeared
 * downslope, which is what gives a run a head and a tail.
 */

import * as THREE from 'three';
import { patchStandard, composeRoughness } from './shader.js';

// Named as terrain_shade, aging, surface_wear and waterside name them,
// so a material wearing several libraries carries ONE world position.
const WORLD_VARYINGS = [
  'varying vec3 vAstraWorld;',
  'varying vec3 vAstraWorldN;',
].join('\n');

// Helpers both patches share, all astraStrata-prefixed: GLSL has one
// global namespace, and a chained patch redefining a neighbour's
// function is a compile error naming neither of them.
const STRATA_HEAD = [
  'float astraStrataFbm(vec2 p) {',
  '  return clamp(astraFbm2(p, 3) / 0.875, 0.0, 1.0);',
  '}',
  // The floor of bed `k`, offset by its own hash. Under half a bed, so
  // the stack stays in order however the offsets fall and the bed
  // holding a coordinate is always within one of its floor().
  'float astraStrataFloor(float k, float s) {',
  '  return k + (astraHash21(vec2(k, s)) - 0.5) * 0.9;',
  '}',
  // Value noise in 3-D, built from the 2-D one: the run field is
  // smeared along the fall line, which is no world axis, so an
  // axis-projected field cannot carry it.
  'float astraStrataN3(vec3 p) {',
  '  float k = floor(p.z), f = p.z - k;',
  '  f = f * f * (3.0 - 2.0 * f);',
  '  return mix(astraNoise2(p.xy + k * 37.13),',
  '             astraNoise2(p.xy + (k + 1.0) * 37.13), f);',
  '}',
  // A run is the SMEAR of a source along the flow: taps UPSLOPE of here
  // with decaying weight, so a feature is long downslope, narrow across
  // it, and tapers out below where it started.
  'float astraStrataRun(vec3 p, vec3 d, float len, int taps) {',
  '  float s = astraStrataN3(p), w = 1.0, a = 1.0, o = 0.0;',
  // Unevenly spaced: at one tap per noise cell they would all land on
  // the same lattice phase and comb the run into blocks. The count is
  // the aspect ratio — a tap cannot reach past its neighbour's cell.
  '  for (int i = 0; i < 6; i++) {',
  '    if (i >= taps) break;',
  '    o += len * (0.11 + 0.035 * float(i));',
  '    a *= 0.78; w += a;',
  '    s += a * astraStrataN3(p - d * o);',
  '  }',
  '  return clamp((s / w - 0.5) * 2.2 + 0.5, 0.0, 1.0);',
  '}',
  // Total curvature in 1/m: how far the normal turns per metre of
  // surface under one pixel. Positive convex, negative the concave that
  // holds water, and ZERO across a hard unwelded edge.
  'float astraStrataCurv(vec3 n, vec3 p) {',
  '  vec3 dx = dFdx(p), dy = dFdy(p);',
  '  float d = dot(dx, dx) + dot(dy, dy);',
  '  return (dot(dFdx(n), dx) + dot(dFdy(n), dy)) / max(d, 1e-12);',
  '}',
  // The same, restricted to ONE surface direction. A lip is convex
  // along the flow and a rib is convex across it, and they do opposite
  // things to a run, so the two halves have to be separable.
  'float astraStrataBend(vec3 n, vec3 p, vec3 d) {',
  '  float ax = dot(dFdx(p), d), ay = dot(dFdy(p), d);',
  '  return (dot(dFdx(n), d) * ax + dot(dFdy(n), d) * ay)',
  '       / max(ax * ax + ay * ay, 1e-12);',
  '}',
  // Only a bend the size of a run is a landform: `k` is curvature in
  // run widths, and the pixel-scale wrinkle under it (k of 4 and up)
  // is not a gully and must not be allowed to draw one.
  'float astraStrataBand(float k) {',
  '  return smoothstep(0.15, 0.80, k) * (1.0 - smoothstep(2.0, 5.0, k));',
  '}',
].join('\n');

// `transformed` is still object-space after <begin_vertex>, so the
// instance transform is folded in by hand or every scattered boulder
// takes its bedding from the mesh origin.
const BASE = {
  name: 'strata:base',
  vertexHead: WORLD_VARYINGS,
  vertexBody: [
    '  vec4 stbP = vec4(transformed, 1.0);',
    '  vec3 stbN = normal;',
    '#ifdef USE_INSTANCING',
    '  stbP = instanceMatrix * stbP;',
    '  stbN = mat3(instanceMatrix) * stbN;',
    '#endif',
    '  vAstraWorld = (modelMatrix * stbP).xyz;',
    '  vAstraWorldN = normalize((modelMatrix * vec4(stbN, 0.0)).xyz);',
  ].join('\n'),
  fragmentHead: [WORLD_VARYINGS, STRATA_HEAD].join('\n'),
};

// A pale sandstone, an iron-rich tan, a grey-green marl and a dark
// red-brown mudstone: enough spread that a thin dark bed between two
// thick pale ones is possible.
//
// The four differ in HUE and in saturation, not only in value. A ramp
// that walks one hue from light to dark is a greyscale ramp with a
// tint on it, and the beds then read as a barcode rather than as
// geology.
//
// They are also authored WARM on purpose, and the reason is measured
// rather than taste. Most of a cliff is in its own shade, and the fill
// this library ships (`sunRig`: a hemisphere at 0x9db8e8 over warm
// ground, plus a blue-sky environment) puts LINEAR (1, 1.56, 2.45) on
// a shaded vertical face — a neutral grey albedo renders (88, 110,
// 134) there. An albedo has to beat that ratio to come back as rock at
// all: at G/R above ~0.64 linear the bed returns GREEN, which is how
// the original palette's mid tones read. These are solved against that
// measurement, so a shaded face returns roughly (1, 0.90, 0.73) for
// the buff down to (1, 0.63, 0.40) for the mudstone — sandstone, and
// under a neutral or overcast rig, red-rock sandstone. The four are
// spread by SATURATION between those, never toward yellow: a bed that
// reaches G/R = 1 in the render is one grain-multiply away from green.
const BEDS = [0xcc9f78, 0x8e6541, 0x74583f, 0x5b3a23];

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
function toColor(value, fallback) {
  return new THREE.Color(
      value === undefined || value === null ? fallback : value);
}

/** Clamp to 0..1 without importing MathUtils for one call. */
function unit(value) {
  return Math.max(0, Math.min(1, value));
}

/** fract(), which JS's % gets wrong for a negative seed. */
function frac(x) {
  return x - Math.floor(x);
}

/** astraHash11 from GLSL_UTIL, so CPU and shader agree on a seed. */
function hash11(x) {
  let p = frac(x * 0.1031);
  p *= p + 33.33;
  return frac(p * (p + p));
}

/**
 * Turn a seed into a noise-space offset, so two rocks differ.
 *
 * The offset is a UNIFORM: a seed baked into the GLSL would be fixed
 * for every material sharing the cache key. The constants differ from
 * aging's and surface_wear's, or one seed would lay these runs down the
 * middle of that library's stains.
 */
function seedOffset(seed, salt) {
  return new THREE.Vector3(
      hash11(seed + salt + 1.63), hash11(seed + salt + 6.41),
      hash11(seed + salt + 11.09)).multiplyScalar(52);
}

/**
 * Resample any palette to the four tones the shader ramps through.
 *
 * The count is fixed because the GLSL is: an option may vary by
 * uniform, never by source, so two cliffs with two-colour and
 * five-colour palettes still share one compiled program.
 */
function palette4(colors) {
  const list = (Array.isArray(colors) && colors.length ? colors : BEDS)
      .map((c) => toColor(c, BEDS[1]));
  if (list.length === 1) return [0, 1, 2, 3].map(() => list[0].clone());
  return [0, 1, 2, 3].map((i) => {
    const t = (i / 3) * (list.length - 1);
    const k = Math.min(Math.floor(t), list.length - 2);
    return list[k].clone().lerp(list[k + 1], t - k);
  });
}

/**
 * The normal of the bedding plane: world up, tipped by `tilt` degrees.
 *
 * The dip DIRECTION comes from the seed rather than from another
 * option, so a scene sets one tilt and one seed and every cliff in it
 * dips the same way.
 */
function beddingUp(tilt, seed) {
  const dip = Math.max(-85, Math.min(85, tilt)) * Math.PI / 180;
  const az = hash11(seed + 0.71) * Math.PI * 2;
  return new THREE.Vector3(
      Math.sin(dip) * Math.cos(az), Math.cos(dip),
      Math.sin(dip) * Math.sin(az)).normalize();
}

/**
 * Lay sedimentary beds through the rock, by world ALTITUDE.
 *
 * A cliff reads as rock because it shows the layers it was deposited
 * in, and the eye checks them against each other: beds have to run
 * THROUGH the scene, so the band at 12 m on this face continues at
 * 12 m on the one across the valley. That is why the coordinate is
 * `dot(worldPosition, beddingNormal)` and nothing else — no UV, no
 * object space, no per-mesh phase — and why every cliff should be
 * given the same options.
 *
 * `tilt` is the whole point. Level beds read as a stripe texture; beds
 * dipping a few degrees read as geology, because the bands then cut
 * across the topography instead of following it. It is a dip in
 * DEGREES, and the dip direction comes from `seed`.
 *
 * Three things keep it from being wallpaper. Bed THICKNESS varies, in
 * two registers: a slow warp of the stack coordinate gives a run of
 * thick beds and then a run of thin ones, and every bed floor carries
 * its own offset on top of that, so a 0.8 m stack measures anywhere
 * from 0.13 m to 1.5 m bed to bed. The bedding surfaces UNDULATE, by
 * `jitter` of a bed over ~30 m of ground, so no boundary is a ruled
 * line. And every so often a contact carries a thin dark PARTING —
 * drawn with `astraStroke`, which fades a stroke out once a pixel spans
 * more than it, so the seams thin away with distance instead of
 * aliasing into moire. Past a pixel per bed the whole stack dissolves
 * into its own mean tone, for the same reason.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` beds every mesh
 *   wearing it, so clone it first if that is not what you want.
 * @param {object} [opts] `spacing` metres of an average bed (default
 *   0.8; thickness varies about it); `tilt` dip of the beds in degrees
 *   (default 7 — 0 is level and reads as texture, past ~25 it reads as
 *   folded); `colors` array of THREE.Color or hex, resampled to the
 *   four tones the beds ramp through (default a buff-to-mudstone run,
 *   authored WARM against this renderer's blue-sky fill — see BEDS; a
 *   palette that is merely "rock-coloured" comes back cyan in shade);
 *   `contrast` how far the albedo goes toward the bed tone, 0..1
 *   (default 0.7); `jitter` how far a bedding surface wanders, in beds
 *   (default 0.35); `seed` moves the tones, the wander and the dip
 *   direction (default 1).
 * @returns {THREE.Material} The same material. Its uniforms stay live
 *   on `material.userData.uniforms`, so `uStrataUp` can be retuned for
 *   a whole scene at once.
 */
export function patchRockStrata(material, opts = {}) {
  const spacing = opts.spacing === undefined ? 0.8 : opts.spacing;
  const tilt = opts.tilt === undefined ? 7 : opts.tilt;
  const contrast = opts.contrast === undefined ? 0.7 : opts.contrast;
  const jitter = opts.jitter === undefined ? 0.35 : opts.jitter;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const beds = palette4(opts.colors);
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'strata:beds',
    uniforms: {
      uStrataUp: { value: beddingUp(tilt, seed) },
      uStrataSpacing: { value: Math.max(1e-3, spacing) },
      uStrataAmt: { value: unit(contrast) },
      uStrataJit: { value: Math.max(0, jitter) },
      uStrataSeed: { value: seedOffset(seed, 0) },
      uStrataC0: { value: beds[0] },
      uStrataC1: { value: beds[1] },
      uStrataC2: { value: beds[2] },
      uStrataC3: { value: beds[3] },
    },
    vertexHead: WORLD_VARYINGS,
    fragmentHead: [
      'uniform vec3 uStrataUp;',
      'uniform float uStrataSpacing;',
      'uniform float uStrataAmt;',
      'uniform float uStrataJit;',
      'uniform vec3 uStrataSeed;',
      'uniform vec3 uStrataC0;',
      'uniform vec3 uStrataC1;',
      'uniform vec3 uStrataC2;',
      'uniform vec3 uStrataC3;',
      // A ramp, not a set: neighbouring beds take unrelated points on
      // it, so a sequence gets both near-twins and hard contacts.
      'vec3 astraStrataTone(float f) {',
      '  float g = f * 3.0;',
      '  vec3 c = mix(uStrataC0, uStrataC1, clamp(g, 0.0, 1.0));',
      '  c = mix(c, uStrataC2, clamp(g - 1.0, 0.0, 1.0));',
      '  return mix(c, uStrataC3, clamp(g - 2.0, 0.0, 1.0));',
      '}',
      // Which point of the ramp a bed takes. A uniform draw lands a
      // quarter of the beds in the palest quarter and a quarter in the
      // darkest, and consecutive beds then alternate near-white and
      // near-black: a barcode, not a formation. This pulls the middle
      // of the draw toward the middle of the ramp and leaves the two
      // ends alone, so most beds are mid-tone and the pale marker bed
      // and the dark parting bed stay rare enough to read as events.
      // Monotone and continuous, so the contact antialiasing below is
      // untouched.
      'float astraStrataPick(float h) {',
      '  float e = abs(2.0 * h - 1.0);',
      '  return 0.5 + (h - 0.5) * (0.45 + 0.55 * e * e);',
      '}',
    ].join('\n'),
    fragmentBody: [
      // Altitude across the beds, in beds. World, and anchored to
      // nothing the mesh knows, so two cliffs cut one stack.
      '  float stB = dot(vAstraWorld, normalize(uStrataUp))',
      '            / uStrataSpacing;',
      // The surfaces undulate over ~30 m of ground: no bed was ever
      // laid down against a ruler.
      '  stB += (astraStrataFbm(vAstraWorld.xz * 0.035',
      '                         + uStrataSeed.xz) - 0.5) * uStrataJit;',
      // A slow stretch of the stack by a function of ITSELF: a run of
      // thick beds, then a run of thin ones, the way a formation reads.
      '  stB += (astraStrataFbm(vec2(stB * 0.16, uStrataSeed.y)) - 0.5)',
      '       * 1.20;',
      // ...and each bed floor carries its OWN offset on top of that, so
      // one bed comes out several times another. A stripe of one width
      // is wallpaper, and this is the line that stops it being one.
      '  float stI = floor(stB);',
      '  float stLo = astraStrataFloor(stI, uStrataSeed.z);',
      '  if (stB < stLo) {',
      '    stI -= 1.0;',
      '    stLo = astraStrataFloor(stI, uStrataSeed.z);',
      '  }',
      '  float stHi = astraStrataFloor(stI + 1.0, uStrataSeed.z);',
      '  if (stB >= stHi) {',
      '    stI += 1.0; stLo = stHi;',
      '    stHi = astraStrataFloor(stI + 1.0, uStrataSeed.z);',
      '  }',
      '  float stTh = max(stHi - stLo, 1e-3);',
      '  float stF = (stB - stLo) / stTh;',
      // Continuous across a contact where stF is not, so the seam's own
      // antialiasing reads a gradient there instead of a jump.
      '  float stP = stI + stF;',
      '  float stAA = clamp(fwidth(stB) / stTh, 1e-4, 2.0);',
      // astraHash21, not astraHash11: over consecutive integers the 1-D
      // one lands 40% of the beds in its palest quarter and the cliff
      // comes out bleached.
      '  float stNb = stI + (stF < 0.5 ? -1.0 : 1.0);',
      // A contact is sharp in the rock, and a sharp edge in a shader is
      // what crawls — MSAA never sees one — so it is resolved over the
      // pixel it actually covers.
      '  vec3 stCol = mix(',
      '      astraStrataTone(astraStrataPick(',
      '          astraHash21(vec2(stI, uStrataSeed.z + 2.3)))),',
      '      astraStrataTone(astraStrataPick(',
      '          astraHash21(vec2(stNb, uStrataSeed.z + 2.3)))),',
      '      0.5 - 0.5 * smoothstep(0.0, stAA, min(stF, 1.0 - stF)));',
      // Grain from world XZ only, so within a bed it streaks ALONG the
      // bedding on any vertical face, and each bed gets its own.
      '  float stG = astraStrataFbm(vAstraWorld.xz * 1.1 + stI * 4.7);',
      // In HUE as well as value: one bed is not one colour across a
      // face — it is warm where the iron collected and washed out where
      // it did not — and a scalar multiply here leaves a whole bed
      // flat, which is what makes a shader stack read as printed
      // stripes. The far end of the grain is BLEACHED, barely cool: on
      // a face already lit by a blue sky a cool multiply is what tips a
      // bed past G/R = 1 and turns it green, and a green patch is not
      // a mineral.
      '  stCol *= mix(vec3(1.12, 1.02, 0.87), vec3(0.95, 0.97, 1.01),',
      '               stG);',
      // A finer mottle on top, so the metre-scale grain is not the
      // smallest thing on the rock at arm's length.
      '  float stG2 = astraStrataFbm(vAstraWorld.xz * 3.7 + stI * 9.1);',
      '  stCol *= mix(0.93, 1.06, stG2);',
      // Every bed is a little darker at its foot, which is the shadow
      // of the ledge the harder bed above it makes — and warmer, since
      // that is where the iron coming down the face stops.
      '  stCol *= mix(vec3(1.0), vec3(1.0, 0.92, 0.80),',
      '               1.0 - smoothstep(0.0, 0.32, stF));',
      '  stCol *= mix(0.93, 1.03, smoothstep(0.0, 0.30, stF));',
      // The occasional thin dark parting at a contact, in fractions of
      // the bed it belongs to; astraStroke fades it out rather than
      // aliasing once a pixel spans more than the seam itself.
      '  float stS = astraHash21(vec2(stI, uStrataSeed.z + 5.1));',
      '  float stSeam = astraStroke(stP + 0.5, mix(0.02, 0.09, stS))',
      '               * smoothstep(0.55, 0.75, stS)',
      // ...and it fades in and out ALONG the bed, on that bed's own
      // grain. A parting of one strength the whole way round is a
      // scribed line, which is the tell that a shader drew the contact.
      '               * mix(0.28, 1.0, stG);',
      // A parting is a thin bed of clay, not a hole punched in the
      // cliff: it goes to a dark, slightly cooler version of the rock
      // above it and keeps a floor, so the seam never crushes to black
      // and never loses its hue.
      '  stCol = mix(stCol,',
      '              stCol * vec3(0.50, 0.49, 0.54)',
      '              + vec3(0.012, 0.011, 0.012), 0.66 * stSeam);',
      // A pixel that spans a bed cannot show one, so the stack
      // dissolves into its own mean tone there instead of into moire —
      // and the far cliff keeps its rock colour rather than reverting.
      '  vec3 stAvg = 0.25 * (uStrataC0 + uStrataC1 + uStrataC2',
      '                       + uStrataC3);',
      '  stCol = mix(stCol, stAvg, smoothstep(0.30, 1.00, stAA));',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, stCol, uStrataAmt);',
    ].join('\n'),
  });
}

/**
 * Cut the runs the rain leaves down a face, along the FALL LINE.
 *
 * Rock does not weather evenly: water sheets off the ledges, gathers
 * into threads and takes the dirt down the steepest path, which is
 * straight down a vertical face and the fall line on anything shallower
 * — never world Y on a slope, and never a UV direction. The fall line
 * is `gravity - normal * dot(gravity, normal)`, and a run is that
 * direction's SMEAR of the field: taps upslope of each fragment with
 * decaying weight, so features come out long downslope, narrow across
 * it, and bend as the surface turns. (Squashing the position along the
 * fall line instead is cheaper and wrong — the lever arm is the whole
 * world position, so a 1% turn of the normal moves the field a whole
 * feature and the surface's own wrinkles get painted on it.)
 *
 * They must not be everywhere, and a fragment cannot look up the slope
 * to find the ledge that fed it. What it CAN see is the way the surface
 * bends under it, so the sources are read from curvature split into its
 * two halves: concave ACROSS the flow is a gully and holds its thread
 * the whole way down, convex ALONG the flow is a lip and pours what it
 * catches over, and convex across is a rib that sheds sideways and
 * stays clean. A source lowers the field's THRESHOLD rather than
 * dimming it, so clean rock keeps a few strong runs and a gully floor
 * darkens broadly. On flat-shaded geometry (`BoxGeometry`, anything
 * unwelded) curvature reads zero and the field alone carries it.
 *
 * The fade downslope is the smear's own decay: each tap upslope arrives
 * weaker than the last, so a run continues below where it started and
 * tapers out, which is flow rather than stripes.
 *
 * Roughness is per MATERIAL, not per pixel (`<color_fragment>` runs
 * before `<roughnessmap_fragment>`), so the wash takes a small polish
 * off the whole surface through `composeRoughness` — a face with water
 * tracks is a face that gets wet.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material streaks every mesh wearing it.
 * @param {object} [opts] `strength` how far a run goes toward `color`,
 *   0..1 (default 0.45); `color` THREE.Color or hex, the mineral the
 *   runs carry — the shader derives a rust and a darker varnish from it
 *   (default a dark iron brown; a NEUTRAL dark here renders as a
 *   slate-blue patch under a blue-sky fill, which is what the reference
 *   grey-brown did); `scale` metres across a run (default 0.8; its
 *   length is ~8x that, and the curvature the sources look for scales
 *   with it); `seed` moves them (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`.
 */
export function patchErosionStreaks(material, opts = {}) {
  const strength = opts.strength === undefined ? 0.45 : opts.strength;
  const scale = opts.scale === undefined ? 0.8 : opts.scale;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  composeRoughness(material, 'strata:erosion',
                   1 - 0.10 * unit(strength));
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'strata:erosion',
    uniforms: {
      uErosAmt: { value: unit(strength) },
      uErosColor: { value: toColor(opts.color, 0x3e2615) },
      uErosScale: { value: Math.max(1e-3, scale) },
      uErosSeed: { value: seedOffset(seed, 3.7) },
    },
    vertexHead: WORLD_VARYINGS,
    fragmentHead: [
      'uniform float uErosAmt;',
      'uniform vec3 uErosColor;',
      'uniform float uErosScale;',
      'uniform vec3 uErosSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec3 erN = normalize(vAstraWorldN);',
      // The fall line is gravity with the normal taken out, and its
      // LENGTH is the sine of the slope — one expression gives the way
      // the water goes and the gate that keeps it off level ground.
      '  vec3 erG = vec3(0.0, -1.0, 0.0) + erN * erN.y;',
      '  float erSin = length(erG);',
      '  vec3 erD = erG / max(erSin, 1e-4);',
      '  vec3 erP = vAstraWorld / uErosScale + uErosSeed;',
      // Long: the smear is what makes a run a run, and at 5.5 it came
      // out as round blotches on a face this size — a stain, not a
      // track. Six taps over 7.5 run widths is an 8:1 feature before
      // the threshold ever sees it.
      '  float erA = astraStrataRun(erP, erD, 7.5, 6);',
      // A finer smear ADDED before the threshold rather than multiplied
      // after: it tears every run's edge into threads that run WITH it.
      // Weighted hard enough to actually break the edge — at 0.26 the
      // broad field won everywhere and the runs closed back into blobs.
      '  float erFib = astraStrataRun(erP * 3.4, erD, 2.8, 4);',
      '  float erF = erA + (erFib - 0.5) * 0.40;',
      // Curvature in run widths, split by direction: a hollow ACROSS
      // the flow holds water, a break ALONG it pours what it caught
      // over, a rib across it sheds sideways and stays clean.
      '  float erAl = astraStrataBend(erN, vAstraWorld, erD) * uErosScale;',
      '  float erAc = astraStrataCurv(erN, vAstraWorld) * uErosScale',
      '             - erAl;',
      '  float erSrc = clamp(0.34 + 0.60 * astraStrataBand(-erAc)',
      '      + 0.40 * astraStrataBand(erAl)',
      '      - 0.32 * astraStrataBand(erAc), 0.0, 1.0);',
      // A source lowers the BAR rather than dimming the run: clean rock
      // keeps only the strongest few. The span is narrow because a
      // screen-derivative gate is flat per TRIANGLE.
      '  float erT = mix(0.72, 0.58, erSrc);',
      '  float erAA = clamp(fwidth(erF), 0.0, 0.5);',
      // Two levels on one field, kept APART rather than summed away: a
      // broad wash and the darker core inside it are different
      // minerals, and the pair is what makes a run read as a run and
      // not as a smudge.
      '  float erBroad = smoothstep(erT - 0.10 - erAA,',
      '                             erT + 0.04 + erAA, erF);',
      '  float erCore = smoothstep(erT + 0.04 - erAA,',
      '                            erT + 0.15 + erAA, erF);',
      '  float erK = 0.45 * erBroad + 0.55 * erCore;',
      // A slope to run down, and not a soffit: what faces down drips
      // clear of the rock instead of tracking across it.
      '  float erFlow = smoothstep(0.10, 0.42, erSin)',
      '               * (1.0 - smoothstep(0.15, 0.65, -erN.y));',
      '  float erAmt = clamp(erK * erFlow * uErosAmt, 0.0, 1.0);',
      // Which mineral this run carries, from a field far coarser than
      // the run itself, so ONE run is one mineral the whole way down
      // instead of speckling along its length: iron leaves a rust
      // margin, manganese the near-black varnish of a desert face.
      // Neither variant is allowed to go neutral: under a blue-sky fill
      // a neutral dark stain renders as a slate-blue patch, which is
      // how a grey-brown default reads on a cliff in its own shade.
      '  float erMin = astraStrataN3(erP * 0.21 + 13.7);',
      '  vec3 erIron = uErosColor * vec3(1.55, 1.15, 0.78);',
      '  vec3 erMang = uErosColor * vec3(0.55, 0.58, 0.72);',
      '  vec3 erTint = mix(erIron, erMang,',
      '                    erCore * mix(0.35, 0.95, erMin));',
      // A wet track DARKENS the rock it runs over and keeps that
      // rock\'s hue; replacing the albedo outright erases the bedding
      // the runs are supposed to run ACROSS, which is what turns a
      // face of runs into a face of grey cloud. So the stain is a
      // darkening of what is underneath, tinted toward the mineral.
      '  vec3 erWet = diffuseColor.rgb * mix(0.30, 0.78, erA);',
      '  vec3 erCol = mix(erWet, erTint * mix(0.78, 1.22, erA), 0.56);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, erCol, erAmt);',
    ].join('\n'),
  });
}
