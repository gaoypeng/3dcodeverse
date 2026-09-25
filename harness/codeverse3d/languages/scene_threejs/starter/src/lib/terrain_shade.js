/**
 * Terrain surfaces that read as rock, not as stretched wallpaper.
 *
 * Both patches here shade from the SURFACE — world position and world
 * normal — so neither needs a UV and the ground and the cliff from
 * `terrain.js` can wear one look at any angle. Each is a `patchStandard`
 * on a material you already built, so the built-in's lighting, shadows,
 * fog and depth chunks stay exactly as they were.
 */

import { patchStandard, toColor, worldBase } from './shader.js';

// The whole interface between the stages: the world varyings every
// library shares, written once by this base.
const WORLD = worldBase('terrain:world', 'astraWp', 'astraWn');

// astraFbm2 sums 0.5, 0.25, ... so its top end moves with the octave
// count; rescaled to 0..1, one mix factor means the same at any count.
const FBM_UNIT = [
  '#ifndef ASTRA_FBM_UNIT',
  '#define ASTRA_FBM_UNIT',
  'float astraFbmUnit(vec2 p, int oct) {',
  '  float footprint = max(length(dFdx(p)), length(dFdy(p)));',
  '  float sum = 0.0, weight = 0.0, amplitude = 0.5, frequency = 1.0;',
  '  for (int i = 0; i < 6; i++) {',
  '    if (i >= oct) break;',
  '    float resolved = 1.0 - smoothstep(0.25, 0.85, footprint * frequency);',
  '    sum += amplitude * mix(0.5, astraNoise2(p * frequency), resolved);',
  '    weight += amplitude; amplitude *= 0.5; frequency *= 2.0;',
  '  }',
  '  return clamp(sum / max(weight, 0.25), 0.0, 1.0);',
  '}',
  '#endif',
].join('\n');

// MINERAL colour, and the reason both patches stopped looking like
// putty.
//
// `astraHueBreak` at one scale is a TINT, not a grain: its field is a
// 2-octave fbm, so at the ~3 m wavelength the two bodies below used to
// ask for, anything boulder-sized lands whole inside a single lobe.
// Measured on this library's showcase before this block: a 1 m boulder
// came out R-B = -32 (cold slate) against the unpatched dirt beside it
// at +21, saturation 0.06-0.09, and a hue spread of 0.02-0.03 across a
// whole region — one flat cold value, which is the exact defect the
// palette probe names.
//
// So: TWO scales — patch-sized over hand-sized — and a mean pulled
// warm.  Rock in daylight is never cooler than the ground beside it;
// the cool half of the swing only happens where the sky alone reaches,
// and the LIGHT does that for free.  The ochre term rides the same
// world field the mesh does, so the warm patches sit on the surface
// rather than on a screen grid.
const GRAIN = [
  '#ifndef ASTRA_TERRAIN_GRAIN',
  '#define ASTRA_TERRAIN_GRAIN',
  'vec3 astraTerrainGrain(vec3 c, vec3 wp, float swing) {',
  // astraHueBreak's own swing is a HALF-amplitude of 0.375 * swing, so
  // splitting one 0.34 call into two smaller ones lost amplitude
  // instead of gaining structure: the first attempt moved rendered
  // saturation by 0.00-0.03 and was invisible. Both scales run at or
  // above the original swing; the two fields are decorrelated, so they
  // stack to mineral movement rather than to one louder tint.
  '  vec3 g = astraHueBreak(c, wp.xz + wp.y * 0.55, 0.40, swing * 0.80);',
  '  g = astraHueBreak(g, wp.zx + wp.y * 0.9 + 27.3, 2.4, swing * 1.15);',
  '  float w = clamp(astraFbmUnit(wp.xz * 0.85 + wp.y * 0.35 + 5.1, 3)',
  '                  * 1.25 - 0.12, 0.0, 1.0);',
  '  return g * mix(vec3(0.92, 0.96, 1.04), vec3(1.21, 1.05, 0.84), w);',
  '}',
  '#endif',
].join('\n');

const TRI_HEAD = [
  'uniform vec3 uTriA;',
  'uniform vec3 uTriB;',
  'uniform float uTriScale;',
  'uniform float uTriSharp;',
  'uniform int uTriOct;',
  'uniform float uTriRelief, uTriRoughness;',
  'vec3 astraTriWeights(vec3 n, float sharp) {',
  '  vec3 w = pow(abs(n), vec3(sharp));',
  '  return w / max(w.x + w.y + w.z, 1e-4);',
  '}',
].join('\n');

const TRI_BODY = [
  '  vec3 tpW = astraTriWeights(normalize(vAstraWorldN), uTriSharp);',
  '  vec3 tpP = vAstraWorld / uTriScale;',
  // One noise read on three planes mirrors itself across the diagonals
  // and the seam shows as a crease; the offsets decorrelate them.
  '  float tpN = astraFbmUnit(tpP.yz + 11.3, uTriOct) * tpW.x',
  '            + astraFbmUnit(tpP.zx + 41.7, uTriOct) * tpW.y',
  '            + astraFbmUnit(tpP.xy + 71.1, uTriOct) * tpW.z;',
  // Kept in a local the SPLAT can read: patch bodies are
  // concatenated into one main(), and a splat that simply
  // assigned over this erased the whole triplanar.
  '  vec3 tpC = mix(uTriA, uTriB, clamp(tpN, 0.0, 1.0));',
  // MASS-scale value, and the up-face wash. A cliff is light and dark
  // ROCK; rain bleaches the ledges pale and leaves the sheltered
  // undersides dark. Without these two the whole wall is one tone
  // dithered — measured at luminance sigma 0.023 across a 13 m face.
  // Kept in `tpV` so a SPLAT landing on top can carry the same
  // structure instead of flattening it back out.
  '  float tpM = astraFbmUnit(tpP.xz * 0.21 + 3.3, 3);',
  '  float tpU = normalize(vAstraWorldN).y * 0.5 + 0.5;',
  '  float tpV = mix(0.80, 1.15, tpM) * mix(0.84, 1.10, tpU);',
  '  tpC *= tpV;',
  // Rock is never one colour at arm's length: a granite face runs pink,
  // grey and near-black across a metre, and the palette probe named
  // exactly this surface "one flat hue".
  '  tpC = astraTerrainGrain(tpC, vAstraWorld, 0.42);',
  '  diffuseColor.rgb = tpC;',
  '  float tpHeight = (tpN - 0.5) * uTriRelief;',
].join('\n');

const SPLAT_HEAD = [
  'uniform vec3 uSplatGrass;',
  'uniform vec3 uSplatScree;',
  'uniform vec3 uSplatRock;',
  'uniform vec3 uSplatSnow;',
  'uniform float uSplatLow;',
  'uniform float uSplatHigh;',
  'uniform float uSplatBlend;',
  'uniform float uSplatSnowY;',
  'uniform float uSplatSnowBand;',
  'uniform vec4 uSplatRoughness;',
].join('\n');

const SPLAT_CORE = [
  '  vec3 spN = normalize(vAstraWorldN);',
  // A threshold on normal.y alone draws a contour line no hillside has;
  // ~12 m of noise wanders it onto the rock itself.
  '  float spJit = (astraFbmUnit(vAstraWorld.xz * 0.08, 3) - 0.5) * 0.16;',
  // 12 m of wander still draws a SMOOTH edge across anything
  // boulder-sized: the grass cap on a 1 m rock came out a lime sticker
  // with a clean rim (measured saturation 0.39 against 0.06 for the
  // rock under it). A hand-scale term breaks that rim into speckle.
  '  float spFine = (astraFbmUnit(vAstraWorld.xz * 1.6',
  '                  + vAstraWorld.y * 0.9 + 4.7, 4) - 0.5) * 0.11;',
  '  float spUp = clamp(spN.y + spJit + spFine, 0.0, 1.0);',
  '  float spB = max(uSplatBlend, 1e-3);',
  '  float spG = smoothstep(uSplatLow - spB, uSplatLow + spB, spUp);',
  '  float spR = 1.0 - smoothstep(uSplatHigh - spB, uSplatHigh + spB, spUp);',
  '  vec3 spC = mix(mix(uSplatScree, uSplatRock, spR), uSplatGrass, spG);',
  // The zone tones are three near-neutral greys and a green; three
  // greys blended by a slope ramp is a flat card whatever the ramp.
  // BEFORE the snow, so a partly covered slope still shows the rock's
  // own mineral colour through the drifts.
  '  spC = astraTerrainGrain(spC, vAstraWorld, 0.45);',
  // MASS scale, ~4.5 m: a hillside is light and dark ground before it
  // is any of these zones, and without it the only value variation was
  // a 1.6 m dither that averages out at any distance (measured modal
  // luminance 0.93 over a whole crag, down to 0.86 with this line).
  '  float spMass = astraFbmUnit(vAstraWorld.xz * 0.22',
  '                              + vAstraWorld.y * 0.09 + 17.9, 3);',
  '  spC *= mix(0.80, 1.18, spMass);',
  // Snow lies only where the slope can hold it, so the same two knobs
  // gate it and the steepest faces stay bare rock above the line.
  '  float spHold = smoothstep(uSplatHigh, uSplatLow, spUp);',
  '  float spSnow = smoothstep(uSplatSnowY - uSplatSnowBand,',
  '                            uSplatSnowY + uSplatSnowBand,',
  '                            vAstraWorld.y + spJit * uSplatSnowBand * 2.8);',
  // Snow is not a white card. Wind packs it into drifts and SCOURS
  // bare patches, so the same field has to move the COVERAGE, not just
  // tint the white: a smooth ramp painted the whole cap one value
  // (measured hue spread 0.003 and luminance sigma 0.018 over an entire
  // peak, a dead frozen frame). Tinting the snow alone did not shift
  // either number; letting rock show through does.
  // The hue break is a TENTH of the rock's — snow that swings hue looks
  // like ice cream — because the cold-shade / warm-sun split of real
  // snow is the LIGHT's job, not the albedo's.
  '  float spDrift = astraFbmUnit(vAstraWorld.xz * 1.15',
  '                               + vAstraWorld.y * 0.5 + 8.3, 4);',
  '  vec3 spSnowC = astraHueBreak(uSplatSnow * mix(0.84, 1.06, spDrift),',
  '                               vAstraWorld.xz, 1.4, 0.09);',
  '  float spCov = clamp(spSnow * spHold * mix(0.45, 1.45, spDrift),',
  '                      0.0, 1.0);',
  '  spC = mix(spC, spSnowC, spCov);',
  '  float spVar = astraFbmUnit(vAstraWorld.xz * 0.6, 3);',
].join('\n');

// The last line, and its only variant. A `.replace` on the assembled
// body was silent when it missed — and a missed replace here is
// exactly the bug the composition comment below describes.
const SPLAT_TAIL = '  diffuseColor.rgb = spC * mix(0.86, 1.14, spVar);';

// Applied over a triplanar, the zone colour must carry that grain
// rather than replace it: the two are a colour and a variation, not
// two colours. `tpV` brings the triplanar's mass-scale value and
// up-face wash through as well, at half strength — without it the
// splat flattened the cliff structure the triplanar had just built.
const SPLAT_TAIL_OVER_TRI =
    '  diffuseColor.rgb = spC * mix(0.86, 1.14, spVar)'
    + ' * mix(0.88, 1.12, clamp(tpN, 0.0, 1.0)) * mix(1.0, tpV, 0.5);';

const SPLAT_BODY = [SPLAT_CORE, SPLAT_TAIL].join('\n');
const SPLAT_BODY_OVER_TRI = [SPLAT_CORE, SPLAT_TAIL_OVER_TRI].join('\n');

// Metres. Far above any authored terrain, yet small enough that the
// +/-5 m snow band survives float32 (at 1e9 it would collapse to NaN).
const SNOW_OFF = 1e6;

/** Clamp to the 1..6 astraFbm2 actually loops over. */
function toOctaves(value) {
  return Math.max(1, Math.min(6, Math.round(value)));
}

/**
 * The world position and normal both patches shade from, applied once.
 *
 * Named, so a material wearing both patches gets one copy: two copies
 * declared the same locals and redefined the same helper, and the
 * material was dead — verified on the GPU by a sibling library that
 * landed on the same bank material.
 */
function withWorld(material) {
  return patchStandard(material, WORLD);
}


/**
 * Project a procedural rock surface on the world axes — no UVs at all.
 *
 * A planar UV smears every steep face (the ground's own UV stretches to
 * a vertical streak on a cliff), so this blends three world-axis noise
 * projections by the world normal instead: a face at ANY angle gets the
 * projection it faces most, and geometry that shares a scale shares a
 * look with no UV to author. Replaces the base color and mineral finish;
 * existing normal maps remain underneath the filtered metric relief.
 *
 * @param {THREE.Material} material A built-in material, patched in place.
 * @param {object} [opts] `scale` world metres per repeat (default 2);
 *   `sharpness` blend exponent, higher = harder plane transitions
 *   (default 4); `colorA`/`colorB` THREE.Color or hex, the low and high
 *   ends of the noise; `noiseOctaves` detail levels 1..6 (default 4);
 *   `relief` surface-gradient height in metres (default .002, zero disables),
 *   `roughness` mineral finish (default .86). Geometry is not displaced.
 *   `name` the program cache key (default 'terrain:triplanar' — every
 *   patch KIND needs its own, or three's per-type program cache serves
 *   one compiled program to both and the second never runs).
 * @returns {THREE.Material} The same material. Its uniforms stay live on
 *   `material.userData.uniforms` for a scene that wants to retune them.
 */
export function patchTriplanar(material, opts = {}) {
  const scale = opts.scale === undefined ? 2 : opts.scale;
  const sharpness = opts.sharpness === undefined ? 4 : opts.sharpness;
  const oct = opts.noiseOctaves === undefined ? 4 : opts.noiseOctaves;
  withWorld(material);
  material.userData.astraTriplanar = true;
  return patchStandard(material, {
    name: opts.name || 'terrain:triplanar',
    uniforms: {
      // HUE-SEPARATED, not two values of one hue. The old pair sat at
      // 33 and 37 degrees, so the three-plane noise produced a value
      // ramp and nothing else — the grain had no colour to move
      // between. A shaded grey-pink at 12 degrees under a buff ochre
      // at 42 gives the mix 30 degrees to travel, which is what a
      // weathered rock face actually does.
      uTriA: { value: toColor(opts.colorA, 0x6d5952) },
      uTriB: { value: toColor(opts.colorB, 0xab9670) },
      uTriScale: { value: Math.max(1e-3, scale) },
      uTriSharp: { value: Math.max(1, sharpness) },
      uTriOct: { value: toOctaves(oct) },
      uTriRelief: { value: Math.max(0, opts.relief ?? 0.002) },
      uTriRoughness: { value: Math.max(0, Math.min(1, opts.roughness ?? 0.86)) },
    },
    fragmentHead: [FBM_UNIT, GRAIN, TRI_HEAD].join('\n'),
    fragmentBody: TRI_BODY,
    roughnessBody: 'roughnessFactor = clamp(uTriRoughness + (tpN - 0.5) * 0.10, 0.04, 1.0);',
    metalnessBody: 'metalnessFactor = 0.0;',
    normalBody: 'normal = astraBump(-vViewPosition, normal, tpHeight);',
  });
}

/**
 * Choose the surface by SLOPE first and height second.
 *
 * Colouring terrain by height alone paints a cliff the same as the
 * meadow beside it at the same altitude; what the eye reads is that
 * nothing grows on a steep face. So grass takes the flats, scree the
 * mid slopes, bare rock the steepest, and snow lands above `snowLine`
 * ONLY where the slope can hold it — the steep faces stay bare rock
 * through the snow line, which is what makes a peak read as a peak.
 * Every boundary is noise-wandered, since a clean contour reads as a
 * decal.
 *
 * @param {THREE.Material} material A built-in material, patched in place.
 * @param {object} [opts] `grass`/`scree`/`rock`/`snow` THREE.Color or
 *   hex; `snowLine` metres of world Y (default Infinity = no snow);
 *   `snowBand` half-width of the snow transition in metres (default 5 —
 *   right for a mountain, and far too wide for anything smaller: at the
 *   default a 9 m spire is snow-hazed from base to tip instead of
 *   capped, so a boulder field or a 10 m crag wants 1..2);
 *   `slopeLow`/`slopeHigh` the normal.y that ends the flats and begins
 *   the bare rock (default 0.75/0.45 — slopeLow is the GENTLER slope, so
 *   it is the LARGER normal.y, and the pair is ordered here because a
 *   GLSL smoothstep with its edges inverted is undefined); `blend`
 *   half-width of both slope transitions in normal.y (default 0.08);
 *   `roughness` optional {grass, scree, rock, snow} finish values (defaults
 *   .94/.9/.8/.92). All zones are dielectric; prior normal maps remain.
 *   `name` the program cache key (default 'terrain:slopeSplat').
 * @returns {THREE.Material} The same material. Its uniforms stay live on
 *   `material.userData.uniforms`, so `uSplatSnowY` can be animated.
 */
export function patchSlopeSplat(material, opts = {}) {
  const low = opts.slopeLow === undefined ? 0.75 : opts.slopeLow;
  const high = opts.slopeHigh === undefined ? 0.45 : opts.slopeHigh;
  const blend = opts.blend === undefined ? 0.08 : opts.blend;
  const line = opts.snowLine;
  const band = opts.snowBand === undefined ? 5 : opts.snowBand;
  withWorld(material);
  // The two are a colour and a variation. Whichever lands second
  // used to assign straight over the first, so on the terrain the
  // prompts recommend — both — one of them was always invisible.
  // A MARK, not a name: patchTriplanar invites a custom `name`, and
  // detecting by name sent the splat straight back to assigning over
  // it the moment anyone used that option.
  const overTri = material.userData.astraTriplanar === true;
  return patchStandard(material, {
    name: opts.name || (overTri ? 'terrain:slopeSplat+tri'
        : 'terrain:slopeSplat'),
    uniforms: {
      // The zones read as MATERIALS, not as one grey at four
      // brightnesses: bare rock is the darkest AND the most saturated
      // (fresh, wet, lichened), scree is pale and dusty and nearly
      // neutral, grass is a dry olive rather than the lime the old
      // 0x55703a made of a boulder cap (saturation 0.48 -> 0.38).
      uSplatGrass: { value: toColor(opts.grass, 0x5f6b42) },
      uSplatScree: { value: toColor(opts.scree, 0xa08e73) },
      uSplatRock: { value: toColor(opts.rock, 0x6e5844) },
      // Snow albedo, not snow LIGHT: 0xeef2f6 is 0.93-0.97 linear, over
      // the 0.8 an unlit dielectric may hold, and it left the cap a
      // dead flat card (hue spread 0.005 across a whole peak). The sun
      // is what makes snow white; a neutral 0.79 keeps the highlight
      // room ACES needs and lets the drift break show.
      uSplatSnow: { value: toColor(opts.snow, 0xcbc9c4) },
      uSplatLow: { value: Math.max(low, high) },
      // Equal slope thresholds otherwise make the snow-holding smoothstep
      // divide by zero, even though the grass/rock ramps still have a blend.
      uSplatHigh: { value: Math.min(low, high, Math.max(low, high) - 1e-4) },
      uSplatBlend: { value: Math.max(1e-3, blend) },
      uSplatSnowY: { value: Number.isFinite(line) ? line : SNOW_OFF },
      // A band that cannot RESOLVE against the off-sentinel makes a
      // smoothstep with equal edges: 0/0, NaN, and a black terrain.
      // float32 steps by 0.125 m at 1e6, so the OFF case floors the
      // band at a metre; with a real snow line any positive band works.
      uSplatSnowBand: {
        value: Number.isFinite(line)
            ? Math.max(1e-2, band) : Math.max(1, band),
      },
      uSplatRoughness: { value: [
        opts.roughness?.grass ?? 0.94, opts.roughness?.scree ?? 0.9,
        opts.roughness?.rock ?? 0.8, opts.roughness?.snow ?? 0.92,
      ].map(value => Math.max(0, Math.min(1, value))) },
    },
    fragmentHead: [FBM_UNIT, GRAIN, SPLAT_HEAD].join('\n'),
    fragmentBody: overTri ? SPLAT_BODY_OVER_TRI : SPLAT_BODY,
    roughnessBody: [
      'float spRough = mix(mix(uSplatRoughness.y, uSplatRoughness.z, spR),',
      '  uSplatRoughness.x, spG);',
      'roughnessFactor = clamp(mix(spRough, uSplatRoughness.w, spCov)',
      '  + (spFine * 0.3 + spMass * 0.02 - 0.01) * (1.0 - spCov), 0.04, 1.0);',
    ].join('\n'),
    metalnessBody: 'metalnessFactor = 0.0;',
  });
}
