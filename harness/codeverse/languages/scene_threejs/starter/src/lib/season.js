/**
 * The two knobs a built scene is POSED with: one that turns its
 * vegetation through the year, one that puts its light at an hour.
 *
 * `patchSeasonTint` goes through `patchStandard`, so the material keeps
 * its lighting, shadows, fog and depth chunks, and it chains with
 * `patchLeafSSS`, `patchWind` and `patchMicroBreakup`. What it does to
 * a colour already in place: it MIXES, never assigns. The season's
 * colour is taken at the surface's OWN brightness, so an earlier
 * patch's light, shade, grain and hue jitter all survive the turn and
 * a sibling patch stays visible — at `amount` 1 only that brightness
 * structure is left of the base, which is why the default is 0.85.
 * Chain it AFTER anything that assigns the base colour (grass.js and
 * flowers.js both write `diffuseColor.rgb =`, so a season tint applied
 * before them is erased).
 *
 * `dayCycle` touches no material and no light. It returns a plain
 * object whose keys are `sunRig()`'s and `worldShell()`'s own option
 * names, so a composer applies it by spreading it into them, and
 * nothing it was handed is mutated.
 */

import * as THREE from 'three';
import { patchStandard } from './shader.js';

const DEG = Math.PI / 180;

// One palette per kind, plus the numbers that decide how the turn is
// BROKEN UP: `grain` metres per leaf cluster, `spread` the width in
// years of the per-leaf date jitter, `twist` the warm/cool chroma
// wobble between neighbours and `keep` how much of the hue an earlier
// patch left here survives the turn. Autumn is TWO anchors — a canopy
// turns to gold AND to red on the same date, and one uniform orange is
// the tell that a global lerp did the work.
//
// Regraded 2026-09-01 for OUR pipeline (ACES, exposure 1.0, no post
// chain, sun 5.4). The reference's autumn pair was a pure yellow and a
// near-pure brick — 0x8f2410 is linear (0.274, 0.018, 0.005), a red
// with no green and no blue at all, so its shade side took nothing
// from the sky and the canopy came back as chocolate. The pair is now
// an amber (hue 40 deg) and a scarlet (hue 10 deg), both with a
// dielectric floor in the channels they do not own; the ground's two
// anchors were 6 degrees of hue apart, which is why an autumn floor
// measured a hue spread of 2.3 degrees over a whole field.
const PALETTES = {
  leaf: { spring: 0x8fbc4a, summer: 0x2f5a24, gold: 0xdca63a,
          red: 0xa23520, winter: 0x685741, grain: 0.34, spread: 0.10,
          pick: [0.30, 0.70], twist: 0.13, keep: 0.40 },
  grass: { spring: 0x7cad35, summer: 0x44631f, gold: 0xab8f3d,
           red: 0x7d4a22, winter: 0x6f6539, grain: 0.20, spread: 0.07,
           pick: [0.10, 0.90], twist: 0.10, keep: 0.35 },
  ground: { spring: 0x5c5238, summer: 0x6b5d3c, gold: 0x7d6630,
            red: 0x5e3823, winter: 0x4e463a, grain: 1.40, spread: 0.05,
            pick: [-0.35, 1.35], twist: 0.12, keep: 0.30 },
};
const KINDS = { leaf: 0, grass: 1, ground: 2 };

const SEASON_VERTEX_HEAD = [
  'uniform vec2 uSeaSeed;',
  'varying vec3 vAstraSeaP;',
  'varying vec3 vAstraSeaW;',
  'varying float vAstraSeaI;',
  'varying float vAstraSeaD;',
  // three applies instanceMatrix in <project_vertex>, after this hook,
  // so a per-INSTANCE date has to fold it in by hand.
  'vec3 astraSeasonWorld(vec3 p) {',
  '#ifdef USE_INSTANCING',
  '  return (modelMatrix * instanceMatrix * vec4(p, 1.0)).xyz;',
  '#else',
  '  return (modelMatrix * vec4(p, 1.0)).xyz;',
  '#endif',
  '}',
].join('\n');

const SEASON_VERTEX_BODY = [
  '  vec3 seO = astraSeasonWorld(vec3(0.0));',
  '  vAstraSeaI = astraHash21(seO.xz * 0.71 + seO.y * 0.13 + uSeaSeed);',
  // The MODEL position, not `transformed`: wind edits transformed, and
  // a leaf whose date came from a moving point swims as it sways.
  '  vAstraSeaP = position;',
  '  vAstraSeaW = astraSeasonWorld(transformed);',
  // Does this asset HAVE a model position to cell a leaf off? A crown
  // whose leaf cards are built in the vertex shader from instance
  // attributes (canopy.js, grass.js) ships `position` as a zeroed
  // attribute, so every card in the draw call lands in one cell and
  // the turn goes flat. Measured 2026-09-01: hue spread 1.3 deg over a
  // whole autumn crown. The flag is per VERTEX and constant over such
  // a card, so the fallback cannot split one leaf in half.
  '  vAstraSeaD = 1.0 - step(1e-8, dot(position, position));',
].join('\n');

const SEASON_FRAGMENT_HEAD = [
  'uniform float uSeaPhase;',
  'uniform float uSeaKind;',
  'uniform float uSeaAmt;',
  'uniform float uSeaGrain;',
  'uniform float uSeaSpread;',
  'uniform vec2 uSeaSeed;',
  'uniform vec2 uSeaPick;',
  'uniform float uSeaTwist;',
  'uniform float uSeaKeep;',
  'uniform vec3 uSeaSpring;',
  'uniform vec3 uSeaSummer;',
  'uniform vec3 uSeaGold;',
  'uniform vec3 uSeaRed;',
  'uniform vec3 uSeaWinter;',
  'varying vec3 vAstraSeaP;',
  'varying vec3 vAstraSeaW;',
  'varying float vAstraSeaI;',
  'varying float vAstraSeaD;',
  'const vec3 ASTRA_SEA_LUMA = vec3(0.2126, 0.7152, 0.0722);',
  // One anchor's share of the year at ring position f (0..4). The
  // S-curve keeps two neighbouring shares summing to 1 while pulling
  // leaves onto the anchors, which is what makes the turn bimodal
  // instead of a smooth wash through orange.
  'float astraSeasonRing(float f, float c) {',
  '  float d = abs(fract((f - c) * 0.25 + 0.5) - 0.5) * 4.0;',
  '  return smoothstep(0.0, 1.0, max(0.0, 1.0 - d));',
  '}',
  // Three decorrelated randoms — date, pigment, value — as cells on
  // the MODEL for a leaf and as a world field for grass and ground,
  // which have no per-leaf geometry to cell off. uSeaKind chooses.
  'vec3 astraSeasonGrain() {',
  '  vec2 c = floor(vAstraSeaP.xz / uSeaGrain)',
  '      + floor(vAstraSeaP.y / uSeaGrain) * vec2(17.0, 41.0);',
  '  vec3 h = vec3(astraHash21(c + uSeaSeed),',
  '                astraHash21(c * 1.73 + uSeaSeed + 7.31),',
  '                astraHash21(c * 2.91 + uSeaSeed + 19.7));',
  // An InstancedMesh shares ONE geometry, so the model cell is the
  // same card for every copy: without this every leaf of a stand
  // picks the same pigment and the only thing that varies is its date.
  '  h = fract(h + vAstraSeaI * vec3(0.618, 0.379, 0.827));',
  // World fallback for an asset with no model position of its own
  // (vAstraSeaD), coarsened to branch scale there so one leaf card
  // still carries one colour and the wind cannot make it swim.
  '  float sw = max(clamp(uSeaKind, 0.0, 1.0), vAstraSeaD);',
  '  float sg = uSeaGrain * (1.0 + 0.8 * vAstraSeaD',
  '      * (1.0 - clamp(uSeaKind, 0.0, 1.0)));',
  '  vec2 w = vAstraSeaW.xz / sg;',
  '  vec3 f = vec3(astraFbm2(w + uSeaSeed, 2),',
  '                astraFbm2(w.yx * 1.31 + uSeaSeed + 13.0, 2),',
  '                astraFbm2(w * 0.61 + uSeaSeed + 29.0, 2));',
  // Value noise clusters hard around 0.5; stretched, or grass and
  // ground turn as one piece while a leaf canopy breaks up.
  '  f = clamp((f - 0.5) * 2.3 + 0.5, 0.0, 1.0);',
  '  return mix(h, f, sw);',
  '}',
  'vec3 astraSeasonHue(float s, float k) {',
  // uSeaPick sharpens the choice: a LEAF commits to one pigment or
  // the other, so a canopy is gold AND red rather than a continuum of
  // orange, while ground litter is the mixture and stays soft.
  '  vec3 aut = mix(uSeaGold, uSeaRed,',
  '                 smoothstep(uSeaPick.x, uSeaPick.y, k));',
  '  float f = fract(s) * 4.0;',
  '  return uSeaSpring * astraSeasonRing(f, 0.0)',
  '      + uSeaSummer * astraSeasonRing(f, 1.0)',
  '      + aut * astraSeasonRing(f, 2.0)',
  '      + uSeaWinter * astraSeasonRing(f, 3.0);',
  '}',
].join('\n');

const SEASON_FRAGMENT_BODY = [
  '  vec3 seG = astraSeasonGrain();',
  // Three scales of unevenness: this copy, this leaf, and a crown-wide
  // drift so a single un-instanced tree still turns from one side.
  '  float seC = astraFbm2(vAstraSeaW.xz * 0.09 + uSeaSeed, 2) - 0.5;',
  '  float seJ = (vAstraSeaI - 0.5) * 1.3 + (seG.x - 0.5) + seC * 0.9;',
  // A leaf turns LATE, never early — the stragglers in a turning
  // canopy are the ones still green — so the jitter LAGS the date and
  // a winter tree carries no spring.
  '  float sePh = uSeaPhase - uSeaSpread * (abs(seJ) - 0.28);',
  '  vec3 seCol = astraSeasonHue(sePh, seG.y);',
  // No two neighbours land on exactly the anchor: a warm/cool wobble
  // about the season's own hue, a few degrees either way. Without it
  // spring, summer and winter are ONE value per date and only autumn
  // (which has a second anchor) carries any hue spread at all.
  //
  // Both this and the value below are CONTINUOUS combinations of the
  // grain. For grass and ground that grain is a smooth world field,
  // and a `fract` on anything drawn from it lays its own contour rings
  // over the terrain — measured, six sawtooth rings per unit of field,
  // a floor that read as plywood.
  '  float seTw = clamp((seG.z - 0.5) * 1.3 + (seG.y - 0.5) * 0.7,',
  '                     -0.5, 0.5) * 2.0;',
  '  seCol *= vec3(1.0 + uSeaTwist * seTw, 1.0, 1.0 - uSeaTwist * seTw);',
  '  float seV = 0.80 + 0.40 * seG.z;',
  '  float seL = dot(diffuseColor.rgb, ASTRA_SEA_LUMA);',
  '  float seCl = dot(seCol, ASTRA_SEA_LUMA);',
  // Taken at the surface's own brightness and MIXED in: everything an
  // earlier patch put here survives the turn as light and shade — and
  // `uSeaKeep` of it survives as HUE too. Brightness alone was not
  // enough: canopy.js spreads its leaves over 0.3 rad of hue and
  // grass.js splits lush from dry, and a turn that kept only their
  // luminance flattened both to one colour per date. The tilt is
  // renormalised to unit luminance, so it moves hue, never level.
  '  vec3 seN = seCol / max(seCl, 1e-4)',
  '      * pow(max(diffuseColor.rgb / max(seL, 1e-4), vec3(0.06)),',
  '            vec3(uSeaKeep));',
  '  seN /= max(dot(seN, ASTRA_SEA_LUMA), 1e-4);',
  '  vec3 seT = seN * seCl * seV * (0.4 + 0.6 * seL / max(seCl, 1e-4));',
  '  diffuseColor.rgb = mix(diffuseColor.rgb, seT, uSeaAmt);',
].join('\n');

/**
 * Turn a vegetation material through the year, unevenly.
 *
 * `season` is one number a whole scene can share, but a canopy that
 * takes it as a global lerp reads as one sheet of orange. Here the
 * date is jittered per INSTANCE (each tree on its own date), per LEAF
 * (cells on the model, so wind does not make them swim) and by a
 * crown-wide drift, and autumn's anchor is a gold/red PAIR picked per
 * leaf — so a stand at the turn carries green, gold and red at once.
 * The jitter widens at the turn and closes in high summer, because a
 * canopy in full leaf really is one green. What an earlier patch left
 * on the surface comes through it: its light and shade, and a share of
 * its hue jitter too, so `makeCanopy`'s broken colour survives the turn
 * instead of being flattened to one brightness per date.
 *
 * A crown whose leaf cards are BUILT in the vertex shader — canopy.js,
 * grass.js — has no model position to cell a leaf off (`position` is a
 * zeroed attribute for the whole draw call), so the per-leaf random
 * falls back to a branch-scale world field there. Without it those
 * crowns turned as one flat sheet: measured on this renderer, 1.3
 * degrees of hue over an entire autumn tree.
 *
 * Winter browns and dulls; it cannot make a tree BARE — no fragment
 * patch removes geometry — so drop the leaf density in the asset for
 * that, and let this colour what is left.
 *
 * Deterministic in `seed`: no PRNG, no time. `season` and `kind` are
 * uniforms, never baked GLSL, so every material in a scene shares one
 * compiled program and one cache key.
 *
 * @param {THREE.Material} material Leaf, grass or ground material,
 *   patched in place — a shared material from `materials.js` takes
 *   every mesh wearing it with it, so clone it first if that is not
 *   what you want. Re-applying retunes rather than chaining twice.
 * @param {object} [opts] `season` 0..1 through the year, wrapping — 0
 *   spring, 0.25 summer, 0.5 autumn, 0.75 winter (default 0.25);
 *   `kind` 'leaf' | 'grass' | 'ground', which picks the palette, the
 *   size of a patch of one colour and how wide the dates spread
 *   (default 'leaf'); `amount` 0..1 how far the turn goes, and how
 *   much of the material's own colour is left (default 0.85 — 1 keeps
 *   only its brightness); `seed` decorrelates two stands (default 1);
 *   `spread` override for the width in years of the date jitter.
 * @returns {THREE.Material} The same material. Its uniforms stay live
 *   on `material.userData.uniforms` for a scene that animates the
 *   year.
 */
export function patchSeasonTint(material, opts = {}) {
  const kind = PALETTES[opts.kind] ? opts.kind : 'leaf';
  const pal = PALETTES[kind];
  const season = opts.season === undefined ? 0.25 : opts.season;
  const amount = opts.amount === undefined ? 0.85 : opts.amount;
  const base = opts.spread === undefined ? pal.spread : opts.spread;
  // Widest at the turn, tight in full leaf: the same jitter all year
  // would put stray gold leaves through high summer.
  const turn = 0.30 + 1.5 * Math.max(nearSeason(season, 0.5),
                                     0.45 * nearSeason(season, 0.75));
  return patchStandard(material, {
    name: 'season:tint',
    uniforms: {
      uSeaPhase: { value: season },
      uSeaKind: { value: KINDS[kind] },
      uSeaAmt: { value: clamp01(amount) },
      uSeaGrain: { value: Math.max(1e-3, pal.grain) },
      uSeaSpread: { value: Math.max(0, base) * turn },
      uSeaSeed: { value: seedOffset(opts.seed === undefined ? 1
                                                            : opts.seed) },
      uSeaPick: { value: new THREE.Vector2(pal.pick[0], pal.pick[1]) },
      uSeaTwist: { value: pal.twist },
      uSeaKeep: { value: pal.keep },
      uSeaSpring: { value: new THREE.Color(pal.spring) },
      uSeaSummer: { value: new THREE.Color(pal.summer) },
      uSeaGold: { value: new THREE.Color(pal.gold) },
      uSeaRed: { value: new THREE.Color(pal.red) },
      uSeaWinter: { value: new THREE.Color(pal.winter) },
    },
    vertexHead: SEASON_VERTEX_HEAD,
    vertexBody: SEASON_VERTEX_BODY,
    fragmentHead: SEASON_FRAGMENT_HEAD,
    fragmentBody: SEASON_FRAGMENT_BODY,
  });
}

// worldShell()'s and sunRig()'s own day / golden / night anchors, so a
// posed scene and an un-posed one meet at noon and at midnight instead
// of drifting apart. Re-read off environment.js MOODS 2026-09-01 (the
// day horizon had drifted to 0xdce9f2, a sixth of a stop paler than
// the dome a caller who does NOT pose gets).
const DAY_ZENITH = 0x5d8fd6;
const DAY_HORIZON = 0xdbe3ea;
const DUSK_ZENITH = 0x3f5a9e;
const DUSK_HORIZON = 0xf2c17e;
const NIGHT_ZENITH = 0x060a18;
const NIGHT_HORIZON = 0x1d2a4a;

// The levels this renderer is calibrated at. environment.js measured
// OUR pipeline (ACES, exposure 1.0, no post chain) and its rigs land
// at day 5.4 key / 1.4 fill, golden 5.6 / 1.1, night 2.2 / 1.0, with
// sunRig clamping anything under 4.5 (day key) or 0.8 (night fill) UP
// because under-filling is the measured failure here. The reference's
// pose was graded for a post chain at exposure 1.1 and asked for 3.3 /
// 1.1 at noon and 0.75 / 0.58 at night: every daylight hour came back
// clamped to the same 4.5 — one flat key from breakfast to dusk — and
// a posed night rendered a third of the brightness of the very rig it
// was meant to replace.
const DAY_KEY = 5.4;
const DUSK_LIFT = 0.35;
const NIGHT_KEY = 2.2;
const DAY_FILL = 1.36;
const NIGHT_FILL = 1.0;

/**
 * Pose a scene's light at an hour — the whole rig, from one number.
 *
 * Colour TEMPERATURE is the cue, so it is computed rather than picked:
 * the sun's colour is a blackbody that runs from ~1900 K on the
 * horizon to ~5800 K overhead, and the SKY that fills its shadows runs
 * the other way, up past 10000 K when the sun is lowest — which is why
 * a low sun is warm and its shadows are blue. Temperature is the whole
 * cue: the LEVELS are this renderer's (`DAY_KEY` below), they hold
 * across the working day and fall only as the sun goes out, because a
 * dimmer noon is not an evening — an evening is a warmer one.
 *
 * Elevation comes from `hour` and `latitude` at the equinox, and the
 * bearing sweeps a half-turn from morning to evening THROUGH the rig's
 * own azimuth, which is taken as noon — so the scene's composition
 * (cameras placed off the sun) still holds at midday and dawn and dusk
 * are not the same frame. Below the horizon the rig becomes a moon on
 * the same bearing: a directional light at a negative elevation lights
 * every surface from underneath.
 *
 * @param {object} [rig] A `sunRig()` result. READ ONLY — only its
 *   `sunDir` is used, for the noon bearing, and it is not mutated.
 *   Absent, the noon bearing is `sunRig()`'s own default of 35 deg.
 * @param {object} [opts] `hour` 0..24, fractional (default 12);
 *   `latitude` degrees, +N (default 40).
 * @returns {object} A plain description, in `sunRig()`'s and
 *   `worldShell()`'s option names so it can be spread into them:
 *   `elevation`/`azimuth` degrees, `sunColor`, `intensity`, `fill`
 *   (= `ambient`), `zenith`/`horizon`, `fogDensity`, `mood`; plus
 *   `sunDir` (normalized, for `makeSky({ sunDir })`), `fogColor`,
 *   `ambient` the ambient level, `ambientColor` the blue a low sun's
 *   shadows take, `kelvin`, `contrast` key over key-plus-fill, `night`,
 *   `hour` and `latitude`. Every colour and vector is a fresh object.
 */
export function dayCycle(rig, opts = {}) {
  const hour = opts.hour === undefined ? 12 : opts.hour;
  const lat = (opts.latitude === undefined ? 40 : opts.latitude) * DEG;
  const h = ((((hour % 24) + 24) % 24) - 12) * Math.PI / 12;
  // Equinox sun in the local horizon frame: south / west / up. Unit by
  // construction, so the elevation is the up component's arcsine.
  const south = Math.sin(lat) * Math.cos(h);
  const west = Math.sin(h);
  const up = Math.cos(lat) * Math.cos(h);
  const sunEl = Math.asin(Math.max(-1, Math.min(1, up))) / DEG;
  const night = sunEl < 0;
  const noonAz = rigAzimuth(rig);
  const azimuth = noonAz + Math.atan2(west, south) / DEG;
  // At night the scene still needs somewhere to light FROM, so the key
  // becomes the moon: the sun's own elevation reflected above the
  // horizon and held between 12 and 58 degrees. That is a KEY, not the
  // sun — reporting it as `elevation` told a caller at 20:00 that the
  // sun stood 22 degrees up.
  const keyElevation = night
      ? Math.max(12, Math.min(58, -sunEl)) : sunEl;
  const elevation = sunEl;

  const day = clamp01(Math.sin(Math.max(0, sunEl) * DEG));
  const low = 1 - clamp01(sunEl / 16);
  const kelvin = night ? 8200
      : 1900 + 3900 * Math.pow(clamp01(sunEl / 50), 0.55);
  const sunColor = blackbody(kelvin);
  // The sky fills what the sun cannot reach, and it runs the OTHER way:
  // the lower and warmer the key, the bluer the shadow it leaves.
  const ambientColor = blackbody(
      night ? 9000 : 6200 + 6000 * (1 - Math.pow(day, 0.45)));
  // Level holds through the working day and falls only in the last
  // three degrees, where the sun really is going out; the cue that
  // says WHICH hour it is, on this pipeline, is temperature, not
  // brightness — golden hour is measurably the more contrasty rig, not
  // the dimmer one (5.6 over 1.1 against 5.4 over 1.4).
  const intensity = night ? NIGHT_KEY
      : (DAY_KEY + DUSK_LIFT * low)
        * (0.35 + 0.65 * clamp01(sunEl / 3.5));
  const ambient = night ? NIGHT_FILL
      : DAY_FILL - 0.34 * (1 - Math.pow(day, 0.6));

  const lit = Math.pow(day, 0.42);
  const zenith = new THREE.Color(NIGHT_ZENITH)
      .lerp(new THREE.Color(DAY_ZENITH), lit)
      .lerp(new THREE.Color(DUSK_ZENITH), low * lit * 0.5);
  // The low-sun band is worldShell's own golden horizon pulled a third
  // of the way to the sun's measured colour, so the dome and the key
  // cannot disagree about how warm the hour is. The old target was the
  // sun mixed with WHITE, which washed the one band of a dusk frame
  // that is supposed to carry its colour.
  const horizon = new THREE.Color(NIGHT_HORIZON)
      .lerp(new THREE.Color(DAY_HORIZON), lit)
      .lerp(new THREE.Color(DUSK_HORIZON).lerp(sunColor, 0.33),
            low * lit * 0.85);
  return {
    hour, latitude: lat / DEG, night,
    mood: night ? 'night' : (sunEl < 12 ? 'golden' : 'day'),
    elevation, keyElevation, azimuth, sunColor, intensity,
    fill: ambient, ambient, ambientColor,
    zenith, horizon, fogColor: horizon.clone(),
    fogDensity: 0.0018 + 0.0004 * low * lit + 0.0007 * (1 - lit),
    // The direction to light FROM — the moon once the sun is down.
    sunDir: new THREE.Vector3(
        Math.cos(keyElevation * DEG) * Math.cos(azimuth * DEG),
        Math.sin(keyElevation * DEG),
        Math.cos(keyElevation * DEG) * Math.sin(azimuth * DEG)),
    kelvin, contrast: intensity / (intensity + ambient),
  };
}

/** Clamp to 0..1 without importing MathUtils for four calls. */
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

/** 1 at season `c`, falling to 0 a quarter-year either side. */
function nearSeason(s, c) {
  const d = Math.abs((((s - c) % 1) + 1.5) % 1 - 0.5);
  return Math.max(0, 1 - d * 4);
}

/** A stable vec2 field offset per seed — no PRNG, no clock. */
function seedOffset(seed) {
  const h = (n) => {
    const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
    return x - Math.floor(x);
  };
  return new THREE.Vector2(h(seed) * 64, h(seed * 1.7 + 3.3) * 64);
}

/** The rig's noon bearing, in `sunRig()`'s azimuth convention. */
function rigAzimuth(rig) {
  const d = rig && rig.sunDir;
  if (!d || (d.x === 0 && d.z === 0)) return 35;
  return Math.atan2(d.z, d.x) / DEG;
}

/**
 * Planckian locus as an sRGB colour, normalized to its brightest
 * channel — the colour of the light, not its level, which `intensity`
 * carries. Approximation good over 1000-12000 K.
 */
function blackbody(kelvin) {
  const t = Math.max(1000, Math.min(12000, kelvin)) / 100;
  let r = 1, g, b = 1;
  if (t <= 66) {
    g = 0.3900816 * Math.log(t) - 0.6318414;
    b = t <= 19 ? 0 : 0.5432068 * Math.log(t - 10) - 1.1962541;
  } else {
    r = 1.2929362 * Math.pow(t - 60, -0.1332048);
    g = 1.1298909 * Math.pow(t - 60, -0.0755149);
  }
  return new THREE.Color().setRGB(
      clamp01(r), clamp01(g), clamp01(b), THREE.SRGBColorSpace);
}
