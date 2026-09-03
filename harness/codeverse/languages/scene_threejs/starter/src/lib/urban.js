/**
 * The three things that say CITY rather than a row of boxes: a glazed
 * facade, the cables strung over the street, and the hoarding on the
 * gable end.
 *
 * Each replaces a specific cheat. A curtain wall drawn as one shiny
 * material is one mirror sheet — real glazing is a grid of separately
 * hung panels, each at its own angle and tint, and what it shows
 * depends on where you stand: the sky at grazing angles, the room
 * head-on. Power lines drawn as straight tubes are a truss; a hanging
 * cable is a CATENARY and the eye knows the difference. And a
 * billboard without its structure is a floating decal.
 *
 * COST CONTRACT: `patchCurtainWall` spends NO render target. The scene
 * has exactly one (`lib/mirror.js` `makeMirror`, `lib/wetground.js`
 * `makeMirrorFloor` or an ocean), and a facade is never what should
 * spend it — see the patch's own JSDoc for what it does instead.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

import { patchNeonSpill } from './neon.js';
import { fbm2, mulberry32, noiseDataTexture } from './noise.js';
import {
  composeRoughness, keepOutOfDepthPasses, makeShaderMaterial, patchStandard,
  tickShaders,
} from './shader.js';

const _UP = new THREE.Vector3(0, 1, 0);

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
function toColor(value, fallback) {
  return new THREE.Color(
      value === undefined || value === null ? fallback : value);
}

/** Clamp to 0..1 without importing MathUtils for one call. */
function unit(value) {
  return Math.max(0, Math.min(1, value));
}

/** A point in any accepted spelling as its own Vector3. */
function toVec(p) {
  if (p && p.isVector3) return p.clone();
  if (Array.isArray(p)) return new THREE.Vector3(p[0], p[1], p[2] || 0);
  return new THREE.Vector3(p.x || 0, p.y || 0, p.z || 0);
}

/**
 * A seed becomes a far-apart lattice offset, not a one-cell shift.
 *
 * Seeds 7 and 8 offsetting a hash lattice by 1 would merely TRANSLATE
 * the pattern by one panel, which reads as the same tower twice.
 */
function seedOffset(seed) {
  const s = Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973;
  return ((s * 16807) % 9973) * 0.0517;
}

/** Read a `[w, h]`, `{w, h}` or single number as a metric pair. */
function toPair(value, dw, dh) {
  if (value === undefined || value === null) return [dw, dh];
  if (Array.isArray(value)) {
    return [Math.max(1e-3, value[0] || dw),
            Math.max(1e-3, value[1] === undefined ? dh : value[1])];
  }
  if (typeof value === 'number') return [Math.max(1e-3, value), value];
  return [Math.max(1e-3, value.w === undefined ? dw : value.w),
          Math.max(1e-3, value.h === undefined ? dh : value.h)];
}


// ---------------------------------------------------------------- glass

const CW_VARYINGS = [
  'varying vec3 vCwW;',
  'varying vec3 vCwN;',
].join('\n');

// `transformed` is still object-space at <begin_vertex>, so an
// instanced skyline needs instanceMatrix folded in by hand.
const CW_VERTEX = [
  '  vec4 cwP = vec4(transformed, 1.0);',
  '  vec3 cwNo = normal;',
  '#ifdef USE_INSTANCING',
  '  cwP = instanceMatrix * cwP;',
  '  cwNo = mat3(instanceMatrix) * cwNo;',
  '#endif',
  '  vCwW = (modelMatrix * cwP).xyz;',
  '  vCwN = normalize((modelMatrix * vec4(cwNo, 0.0)).xyz);',
].join('\n');

const CW_HEAD = [
  'uniform vec2 uCwPanel;',
  'uniform vec3 uCwTint;',
  'uniform vec3 uCwMetal;',
  'uniform vec3 uCwSky;',
  'uniform vec3 uCwHaze;',
  'uniform vec3 uCwGround;',
  'uniform vec3 uCwBlindCol;',
  'uniform float uCwMullion;',
  'uniform float uCwReflect;',
  'uniform float uCwBow;',
  'uniform float uCwBlinds;',
  'uniform float uCwCurve;',
  'uniform float uCwSeed;',
  'uniform float uCwGain;',
  'uniform float uCwCoat;',
  // Schlick, with the head-on term the COATING sets rather than glass's
  // own 4% — untinted glass at 4% renders as a black hole, and the
  // grazing limit is 1 for any coating, which is the whole effect.
  'float astraCwFresnel(float ndv, float f0) {',
  '  float c = clamp(1.0 - clamp(ndv, 0.0, 1.0), 0.0, 1.0);',
  '  float c2 = c * c;',
  '  return f0 + (1.0 - f0) * c2 * c2 * c;',
  '}',
  // One key per panel, built from astraHash11 of a linear combination
  // so the same numbers can be read on the CPU.
  'float astraCwKey(float ix, float iy) {',
  '  return astraHash11(ix * 1.0731 + iy * 7.3313 + 0.317);',
  '}',
  'float astraCwJit(float key, float k) {',
  '  return astraHash11(key * 19.73 + k * 3.117 + 0.611);',
  '}',
].join('\n');

const CW_BODY = [
  '  vec3 cwN = normalize(vCwN);',
  '  vec3 cwV = normalize(cameraPosition - vCwW);',
  // dot(p.xz, t) is EXACT arc length along a flat face at any
  // orientation and 0 round a tower, where the azimuth term carries it
  // instead; a facade needs a METRIC grid, never a UV one.
  '  vec2 cwTz = vec2(-cwN.z, cwN.x);',
  '  float cwTl = length(cwTz);',
  '  float cwFace = smoothstep(0.20, 0.55, cwTl);',
  '  vec2 cwT = cwTl > 1e-4 ? cwTz / cwTl : vec2(1.0, 0.0);',
  '  float cwU = dot(vCwW.xz, cwT) + uCwCurve * atan(cwN.z, cwN.x);',
  '  vec2 cwG = vec2(cwU, vCwW.y) / uCwPanel;',
  '  vec2 cwAa = fwidth(cwG);',
  '  vec2 cwId = floor(cwG);',
  '  float cwKey = astraCwKey(cwId.x + uCwSeed, cwId.y - uCwSeed);',
  // Per-panel detail is texture until a pixel spans a panel and static
  // after it, so every panel-scale term fades to its mean exactly there.
  '  float cwSharp = 1.0 - smoothstep(0.30, 0.85, max(cwAa.x, cwAa.y));',
  '  float cwMull = max(astraStroke(cwG.x, uCwMullion * 0.5 / uCwPanel.x),',
  '                     astraStroke(cwG.y, uCwMullion * 0.5 / uCwPanel.y));',
  '  cwMull *= cwFace;',
  // Every panel hangs at its own angle. Without this the whole wall
  // shares one reflected ray and reads as a single mirror sheet.
  '  vec3 cwTan = vec3(cwT.x, 0.0, cwT.y);',
  '  vec3 cwBi = cross(cwN, cwTan);',
  '  float cwB1 = (astraCwJit(cwKey, 1.0) - 0.5) * 2.0 * uCwBow * cwSharp;',
  '  float cwB2 = (astraCwJit(cwKey, 2.0) - 0.5) * 2.0 * uCwBow * cwSharp;',
  '  vec3 cwPn = normalize(cwN + cwTan * cwB1 + cwBi * cwB2);',
  '  float cwF0 = uCwReflect * mix(0.82, 1.18,',
  '      astraCwJit(cwKey, 3.0) * cwSharp + 0.5 * (1.0 - cwSharp));',
  '  float cwFr = astraCwFresnel(dot(cwPn, cwV), clamp(cwF0, 0.0, 1.0));',
  '  vec3 cwRay = reflect(-cwV, cwPn);',
  '  vec3 cwRefl = mix(uCwGround, uCwHaze,',
  '                    smoothstep(-0.30, 0.01, cwRay.y));',
  '  cwRefl = mix(cwRefl, uCwSky, smoothstep(0.0, 0.55, cwRay.y));',
  // Glass FILTERS what is behind it — the room patchWindowInteriors put
  // in diffuseColor — and never replaces it; the tint is a hue, so a
  // dark green glass does not black the interior out.
  '  float cwTm = max(max(uCwTint.r, uCwTint.g), max(uCwTint.b, 1e-3));',
  '  vec3 cwFilt = mix(vec3(1.0), uCwTint / cwTm, 0.85);',
  // A coating reflects a TINTED image. Left untinted, a tower under a
  // hazy sky returns the haze and reads as one grey sheet whatever the
  // glass is — which is why real glazing reads green, bronze or blue
  // from across the street and this did not.
  '  cwRefl *= mix(vec3(1.0), uCwTint / cwTm, uCwCoat);',
  // Coating batch varies unit to unit: brightness AND a warm/cool tilt,
  // both fading to their mean when a pixel spans a panel. This is what
  // stops a hundred panels sharing one colour.
  '  float cwJb = astraCwJit(cwKey, 7.0) - 0.5;',
  '  float cwJh = (astraCwJit(cwKey, 8.0) - 0.5) * cwSharp;',
  '  cwRefl *= (1.0 + 0.26 * cwJb * cwSharp)',
  '      * vec3(1.0 + 0.17 * cwJh, 1.0, 1.0 - 0.17 * cwJh);',
  '  vec3 cwGl = diffuseColor.rgb * cwFilt;',
  // A few panels have the blind down: the one thing that DOES hide the
  // room, and the cheapest proof the panels are not one surface. It is
  // an interior surface, so it is filtered by the same glass.
  '  float cwBl = step(astraCwJit(cwKey, 4.0), uCwBlinds * cwSharp);',
  '  float cwDrop = 0.30 + 0.50 * astraCwJit(cwKey, 5.0);',
  '  float cwEdge = max(cwAa.y, 0.01);',
  '  cwBl *= smoothstep(1.0 - cwDrop - cwEdge, 1.0 - cwDrop + cwEdge,',
  '                     fract(cwG.y));',
  '  cwGl = mix(cwGl, uCwBlindCol * cwFilt',
  '      * (0.26 + 0.22 * astraCwJit(cwKey, 6.0)), cwBl);',
  '  float cwGlass = cwFace * (1.0 - cwMull);',
  '  diffuseColor.rgb = mix(diffuseColor.rgb, cwGl, cwGlass);',
  '  diffuseColor.rgb = mix(diffuseColor.rgb, uCwMetal, cwMull);',
  // What the mirror TAKES out of the transmitted image, and what it
  // returns: reflected radiance, which no albedo term can carry — a
  // facade in shadow still mirrors the sky.
  '  float cwRw = cwFr * cwGlass;',
  '  diffuseColor.rgb *= 1.0 - 0.85 * cwRw;',
  '  totalEmissiveRadiance += cwRefl * (cwRw * uCwGain);',
].join('\n');

/**
 * Glaze a facade: mullion grid, fresnel sky, and panels that differ.
 *
 * A curtain wall shaded as one smooth material is one MIRROR SHEET, and
 * that is the tell however good the reflection is. Real glazing is a
 * grid of separately hung units: each panel sits at a slightly
 * different angle, carries a slightly different tint and coating batch,
 * and every fifth one has the blind down. This lays that grid on in
 * world METRES (never UV — a facade merged from boxes carries a full
 * 0..1 UV per box), varies it per panel, and reflects the sky through a
 * Schlick fresnel so the tower shows the sky at grazing angles and its
 * own interior head-on.
 *
 * NO RENDER TARGET, which is the constraint that shapes it. The scene
 * gets ONE whole-scene reflection pass and a facade must never be what
 * spends it: `mirror.js` `makeMirror` re-renders the scene into a
 * Reflector (+48 ms/frame measured, and it enforces a budget of one),
 * and `wetground.js` `makeMirrorFloor` spends that one on the ground.
 * The other two ways out both cost something this does not:
 * `envMirrorMaterial` / `glassFacadeMaterial` need an equirect BAKE and
 * hand three's IBL a roughness-blurred lookup that cannot show the
 * per-panel break-up, and neither shows the room behind the glass. Here
 * the reflection is an ANALYTIC sky evaluated per fragment from the
 * reflected ray — no target, no texture, no bake. The honest trade: it
 * reflects sky, haze and ground, never the building across the street.
 * Pair it with an `envMap` when you want that; three's IBL runs
 * underneath this untouched.
 *
 * COMPOSE, do not duplicate: apply `patchWindowInteriors` FIRST and
 * this second. It never assigns over `diffuseColor.rgb` — it FILTERS
 * what is already there by the glass tint, dims it by what the mirror
 * takes, and adds the reflection through `totalEmissiveRadiance`. So
 * the rooms that patch shades stay visible head-on and are progressively
 * replaced by sky toward grazing, which is what the eye reads as glass.
 * It chains with `patchMicroBreakup` the same way.
 *
 * @param {THREE.Material} material A lit built-in material (MeshStandard
 *   / MeshPhysical — the reflection needs `totalEmissiveRadiance`),
 *   patched in place. Clone a shared `materials.js` instance first.
 * @param {object} [opts] `mullion` width of the aluminium member in
 *   metres (default 0.07); `panel` glazing unit size in metres, `[w, h]`
 *   / `{w, h}` / one number (default 1.5 x 3.6 — a floor-height unit);
 *   `tint` glass colour, used as a HUE the glass filters by (default
 *   0x33556b); `reflect` head-on reflectance 0..1, the coating (default
 *   0.22; 0.04 is uncoated glass, 0.45 a mirror-glass tower — the
 *   grazing limit is 1 whatever this is); `sky` / `haze` / `ground` what
 *   the reflected ray finds looking up, along the horizon and down
 *   (defaults a daylight sky; pass a night sky and the same glass reads
 *   as night); `bow` radians each panel may be off true (default 0.020,
 *   about a degree — 0 gives back the mirror sheet); `blinds` fraction
 *   of panels with the blind down (default 0.16); `curve` radius in
 *   `coat` how much of the glass tint the REFLECTED image carries, 0
 *   an uncoated pane that returns the sky's own colour to 1 a fully
 *   tinted mirror (default 0.45 — at 0 a tower under a hazy sky reads
 *   as one grey sheet whatever `tint` says); `curve` radius in
 *   metres of a ROUND tower, so the mullion pitch follows the arc
 *   instead of the chord (default 0, flat; the azimuth seam falls at
 *   -X); `metal` mullion colour (default 0x8d949a); `blindCol` (default
 *   0xcfc7b6); `gain` reflection brightness (default 1); `seed` moves
 *   which panels differ how (default 1); `name` the program cache key
 *   (default 'urban:curtain' — every option here is a UNIFORM, so one
 *   name is correct and two differently tuned towers share one compiled
 *   program).
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms` so a scene can swing `uCwSky` from day
 *   to dusk without rebuilding anything.
 */
export function patchCurtainWall(material, opts = {}) {
  const panel = toPair(opts.panel, 1.5, 3.6);
  const mullion = opts.mullion === undefined ? 0.07 : opts.mullion;
  const sky = toColor(opts.sky, 0x7ba6d8);
  const haze = opts.haze === undefined
      ? sky.clone().lerp(new THREE.Color(0xffffff), 0.62)
      : toColor(opts.haze, 0xdfe7ee);
  const reflect = opts.reflect === undefined ? 0.22 : unit(opts.reflect);
  // Glass is the smoothest thing on a street; roughness is a MATERIAL
  // property here (no patch can reach the per-pixel one).
  const base = material.userData.astraRoughness
      ? material.userData.astraRoughness.base : material.roughness;
  composeRoughness(material, 'urban:curtain',
                   base > 0 ? Math.max(0.05, 0.10 / base) : 1);
  return patchStandard(material, {
    name: opts.name || 'urban:curtain',
    uniforms: {
      uCwPanel: { value: new THREE.Vector2(panel[0], panel[1]) },
      uCwTint: { value: toColor(opts.tint, 0x33556b) },
      uCwMetal: { value: toColor(opts.metal, 0x8d949a) },
      uCwSky: { value: sky },
      uCwHaze: { value: haze },
      uCwGround: { value: toColor(opts.ground, 0x4b4740) },
      uCwBlindCol: { value: toColor(opts.blindCol, 0xcfc7b6) },
      // Clamped under half the panel: a mullion wider than that is a
      // wall with slots in it, and astraStroke only vanishes cleanly
      // for a THIN stroke.
      uCwMullion: { value: Math.max(
          0, Math.min(mullion, Math.min(panel[0], panel[1]) * 0.25)) },
      uCwReflect: { value: reflect },
      uCwBow: { value: Math.max(0, opts.bow === undefined ? 0.020
                                                          : opts.bow) },
      uCwBlinds: { value: unit(opts.blinds === undefined ? 0.16
                                                         : opts.blinds) },
      uCwCurve: { value: Math.max(0, opts.curve || 0) },
      uCwSeed: { value: seedOffset(opts.seed) },
      uCwGain: { value: Math.max(0, opts.gain === undefined ? 1
                                                            : opts.gain) },
      uCwCoat: { value: unit(opts.coat === undefined ? 0.45 : opts.coat) },
    },
    vertexHead: CW_VARYINGS,
    vertexBody: CW_VERTEX,
    fragmentHead: [CW_VARYINGS, CW_HEAD].join('\n'),
    fragmentBody: CW_BODY,
  });
}


// ---------------------------------------------------------------- cable

/**
 * The catenary parameter `a` for a span that sags `sag` at midspan.
 *
 * sag = a * (cosh(L / 2a) - 1) has no closed form, so it is bisected on
 * u = L / 2a, where the left side rises monotonically from 0.
 *
 * @param {number} span Horizontal distance between the ends, metres.
 * @param {number} sag Midspan dip below the chord, metres.
 * @returns {number} `a` in metres; Infinity for a straight cable.
 */
function catenaryA(span, sag) {
  if (!(sag > 1e-6) || !(span > 1e-6)) return Infinity;
  let lo = 1e-6;
  let hi = 1;
  while (span / (2 * hi) * (Math.cosh(hi) - 1) < sag && hi < 1e4) hi *= 2;
  for (let i = 0; i < 80; i++) {
    const mid = 0.5 * (lo + hi);
    if (span / (2 * mid) * (Math.cosh(mid) - 1) < sag) lo = mid;
    else hi = mid;
  }
  return span / (2 * (0.5 * (lo + hi)));
}

/**
 * Sample the true hanging curve between two points.
 *
 * A parabola is the giveaway: it is the small-sag limit of this, and it
 * gets the CURVATURE wrong — constant along the span where a catenary's
 * grows as cosh, so the cable leaves the insulator at the wrong angle.
 *
 * @param {THREE.Vector3} a One attachment point.
 * @param {THREE.Vector3} b The other, at any height.
 * @param {number} sag Midspan dip in metres.
 * @param {number} n Segments.
 * @returns {THREE.Vector3[]} n + 1 points from `a` to `b`.
 */
function catenaryPoints(a, b, sag, n) {
  const span = Math.hypot(b.x - a.x, b.z - a.z);
  const par = catenaryA(span, sag);
  const out = [];
  if (!Number.isFinite(par)) {
    for (let i = 0; i <= n; i++) {
      out.push(a.clone().lerp(b, i / n));
    }
    return out;
  }
  // Fit the SAME curve to unequal end heights by sliding its low point,
  // which is what a cable on a slope does.
  const k = Math.asinh((b.y - a.y)
      / (2 * par * Math.sinh(span / (2 * par))));
  const p = span / 2 - par * k;
  const q = a.y - par * Math.cosh(p / par);
  for (let i = 0; i <= n; i++) {
    const s = (i / n) * span;
    out.push(new THREE.Vector3(
        a.x + (b.x - a.x) * (i / n),
        par * Math.cosh((s - p) / par) + q,
        a.z + (b.z - a.z) * (i / n)));
  }
  return out;
}

const CABLE_VARYINGS = [
  'varying float vCbSide;',
  'varying float vCbUp;',
  'varying float vCbFade;',
].join('\n');

const CABLE_VHEAD = [
  'attribute vec3 aCbTan;',
  'attribute vec3 aCbRide;',
  'uniform float uCbRadius;',
  'uniform float uCbMinPx;',
  'uniform float uCbViewH;',
  'uniform float uCbSway;',
  // A conductor is ~1 cm across and a pixel at 80 m is 10, so a
  // world-width cable breaks into dashes exactly where a skyline needs
  // it. Widen it to a floor of `minPx` and pay for it in alpha.
  'float astraCbHalf(float depth, float perPx, float radius,',
  '                  float minPx) {',
  '  return max(radius, minPx * perPx * depth);',
  '}',
].join('\n');

const CABLE_VERTEX = [
  '  vec3 cbW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
  '  mat3 cbM = mat3(modelMatrix);',
  '  float cbSc = max(length(cbM[0]), 1e-6);',
  '  vec3 cbT = normalize(cbM * aCbTan);',
  // Wind: the span swings about its ends, so the envelope rides in on
  // the attribute and the phase decorrelates neighbouring conductors.
  '  vec3 cbLat = normalize(cross(cbT, vec3(0.0, 1.0, 0.0)) + 1e-6);',
  '  float cbSw = sin(uTime * 0.63 + aCbRide.x) * uCbSway * aCbRide.y;',
  '  vec3 cbOff = cbLat * cbSw',
  '      + vec3(0.0, cos(uTime * 0.41 + aCbRide.x) * uCbSway * 0.35',
  '                  * aCbRide.y, 0.0);',
  '  cbW += cbOff;',
  '  vec3 cbV = normalize(cameraPosition - cbW);',
  '  vec3 cbSd = cross(cbT, cbV);',
  '  float cbSl = length(cbSd);',
  '  cbSd = cbSl > 1e-5 ? cbSd / cbSl : vec3(1.0, 0.0, 0.0);',
  '  float cbDepth = max(-(viewMatrix * vec4(cbW, 1.0)).z, 1e-3);',
  // Metres per pixel comes from the camera's OWN projection, so a wide
  // lens and a long lens both get the same on-screen thickness.
  '  float cbPerPx = 2.0 / (max(projectionMatrix[1][1], 1e-4) * uCbViewH);',
  '  float cbHalf = astraCbHalf(cbDepth, cbPerPx, uCbRadius, uCbMinPx);',
  '  vCbFade = clamp(uCbRadius / cbHalf, 0.35, 1.0);',
  '  vCbSide = aCbRide.z;',
  '  vCbUp = aCbRide.z * cbSd.y;',
  '  cbOff += cbSd * (aCbRide.z * cbHalf);',
  // Back to object space: exact for the rigid-plus-uniform-scale
  // placement a group gets, which is every placement in this engine.
  '  transformed += (transpose(cbM) * cbOff) / (cbSc * cbSc);',
].join('\n');

const CABLE_FRAGMENT = [
  // The edge feather is capped and then DIVIDED OUT of the alpha: at a
  // 1.5 px ribbon the feather spans the whole width, and left
  // uncompensated it threw away three quarters of the ink the cable is
  // owed — a line that is present and invisible is still a dashed line.
  '  float cbAa = clamp(fwidth(vCbSide) * 1.5, 0.02, 0.7);',
  '  float cbA = (1.0 - smoothstep(1.0 - cbAa, 1.0, abs(vCbSide)))',
  '      / (1.0 - 0.5 * cbAa);',
  '  cbA = min(cbA * vCbFade, 1.0);',
  '  if (cbA < 0.004) discard;',
  // A round wire: the near side of the cylinder catches the sky and the
  // far side is its own silhouette.
  '  float cbRound = sqrt(max(1.0 - vCbSide * vCbSide, 0.0));',
  '  vec3 cbCol = uCbColor * (0.62 + 0.38 * vCbUp)',
  '      * (0.72 + 0.28 * cbRound);',
  '  gl_FragColor = vec4(cbCol, cbA);',
].join('\n');

/**
 * Build the ribbon: one strip per strand, position on the CENTRELINE.
 *
 * Both sides of the strip share the centreline and the vertex shader
 * spreads them, so the GTAO override pass — which draws raw `position`
 * — sees a zero-area strip instead of a wall, and the bounding box
 * measures the cable rather than its widened silhouette.
 */
function cableGeometry(strands) {
  const pos = [], tan = [], ride = [], idx = [];
  let base = 0;
  for (const s of strands) {
    const n = s.pts.length;
    for (let i = 0; i < n; i++) {
      const p = s.pts[i];
      const a = s.pts[Math.max(0, i - 1)];
      const b = s.pts[Math.min(n - 1, i + 1)];
      const t = new THREE.Vector3().subVectors(b, a);
      if (t.lengthSq() < 1e-12) t.set(1, 0, 0);
      t.normalize();
      const env = Math.sin(Math.PI * (i / (n - 1)));
      for (let k = 0; k < 2; k++) {
        pos.push(p.x, p.y, p.z);
        tan.push(t.x, t.y, t.z);
        ride.push(s.phase, env, k === 0 ? -1 : 1);
      }
      if (i < n - 1) {
        const a0 = base + i * 2;
        idx.push(a0, a0 + 1, a0 + 2, a0 + 1, a0 + 3, a0 + 2);
      }
    }
    base += n * 2;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('aCbTan', new THREE.Float32BufferAttribute(tan, 3));
  g.setAttribute('aCbRide', new THREE.Float32BufferAttribute(ride, 3));
  g.setIndex(idx);
  g.computeBoundingSphere();
  return g;
}

/**
 * Weathering in a vertex colour: damp at the foot, sun-silvered up.
 *
 * A run of poles merged into one mesh is ONE flat brown, and that is
 * what says "the same prop, twenty times" from across the street. Real
 * timber differs pole to pole (creosote batch, how long it has stood)
 * and along its own length (the bottom metre stays wet, the top bleaches
 * grey). This is the whole of that fix and it costs nothing: no extra
 * draw call, no texture, and `mergeGeometries` needs the attribute on
 * every piece anyway. It only ever DARKENS — the tone runs down from 1
 * and the warm/cool tilt is normalised — so `color` stays the material's
 * true albedo instead of quietly becoming a floor under a brighter one.
 *
 * @param {THREE.BufferGeometry} geo Already translated into place.
 * @param {() => number} rand The run's seeded stream.
 * @param {number} foot World y the piece rises from.
 * @param {object} [opts] `spread` piece-to-piece tone range, `tilt`
 *   warm/cool range, `damp` metres the wet foot reaches.
 * @returns {THREE.BufferGeometry} The same geometry.
 */
function paintPiece(geo, rand, foot, opts = {}) {
  const pos = geo.attributes.position;
  const col = new Float32Array(pos.count * 3);
  const tone = 1 - rand()
      * (opts.spread === undefined ? 0.24 : opts.spread);
  const tilt = (rand() - 0.5) * (opts.tilt === undefined ? 0.10 : opts.tilt);
  const damp = Math.max(0.05, opts.damp === undefined ? 1.6 : opts.damp);
  // The tilt is a HUE, not a gain: normalised, so the warm channel
  // reaches 1 and the cool one falls, and neither lifts the albedo.
  const norm = 1 / (1 + Math.abs(tilt));
  for (let i = 0; i < pos.count; i++) {
    const h = unit((pos.getY(i) - foot) / damp);
    const v = tone * (0.78 + 0.22 * h);
    col[i * 3] = v * (1 + tilt) * norm;
    col[i * 3 + 1] = v * norm * (1 + Math.abs(tilt) * 0.5);
    col[i * 3 + 2] = v * (1 - tilt) * norm;
  }
  geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  return geo;
}

/** A box as geometry, yawed about Y and positioned by its centre. */
function _box(w, h, d, x, y, z, yaw) {
  const g = new THREE.BoxGeometry(w, h, d);
  if (yaw) g.rotateY(yaw);
  g.translate(x, y, z);
  return g;
}

/** Resample a route into `spans` equal-length legs, ends included. */
function resample(points, spans) {
  const seg = [];
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const d = points[i].distanceTo(points[i - 1]);
    seg.push(d);
    total += d;
  }
  if (!(total > 0)) return points.map((p) => p.clone());
  const out = [];
  for (let k = 0; k <= spans; k++) {
    let want = (k / spans) * total;
    let i = 0;
    while (i < seg.length - 1 && want > seg[i]) { want -= seg[i]; i++; }
    out.push(points[i].clone().lerp(
        points[i + 1], Math.min(1, want / Math.max(seg[i], 1e-9))));
  }
  return out;
}

/**
 * Cables between poles, hanging in a true CATENARY.
 *
 * Two things carry this and neither is the geometry of a tube. The
 * curve is the catenary a hanging chain actually makes, not the
 * parabola that is its small-sag limit — the parabola's curvature is
 * constant along the span, so its cable leaves the insulator at the
 * wrong angle and the eye reads a drawn arc. And the cable is drawn as
 * a CAMERA-FACING ribbon whose half-width has a floor in PIXELS: a real
 * conductor is about a centimetre across, a pixel at 80 m is ten, and a
 * world-width tube therefore breaks into dashes exactly where a skyline
 * needs it. The ribbon widens to that floor and gives back the extra
 * width in alpha, so the line stays continuous and simply gets fainter.
 *
 * The poles come with it: tapered shafts, crossarms square to the run,
 * and a ceramic insulator under every conductor.
 *
 * @param {object} [opts] `points` the route as pole bases, `[[x,y,z],
 *   ...]` or Vector3s — y is the GROUND there (default a 44 m run);
 *   `spans` resample the route into this many equal legs, so two points
 *   and `spans: 4` give five poles (default: a pole per point); `sag`
 *   midspan dip in metres (default 4% of the mean span — a slack line
 *   is 8%, a taut transmission line 2%); `thickness` cable DIAMETER in
 *   metres (default 0.04); `minPixels` the width the cable never falls
 *   below on screen (default 1.5 — this is what stops the aliasing, set
 *   0 for the honest sub-pixel cable and its dashes); `viewportHeight`
 *   the frame height in px that width is measured against (default 576,
 *   the renderer's; `userData.setViewport(px)` retunes it);
 *   `poles` false for bare cables, or `{height, arms, wires, spread,
 *   radius, color}` (default 8.5 m, 2 arms, 3 wires an arm, 2.4 m
 *   spread — the poles are still what SPACES the cables when this is
 *   false; the pole `color` is the timber's albedo, which per-pole
 *   weathering DARKENS in a vertex colour so a run of them is never one
 *   flat brown); `color` conductor colour (default 0x15181c); `sway` metres of
 *   wind swing at midspan, driven by `tick` (default 0.05); `seed`
 *   detunes each conductor's sag and swing (default 1).
 * @returns {THREE.Group} Named `PowerLines`, resting on the y of its
 *   points, with `userData.tick(t)` (the sway), `userData.setViewport(px)`
 *   and `userData.spans` — the measured span lengths. The cable mesh is
 *   guarded by `keepOutOfDepthPasses`: a transparent ribbon is a solid
 *   wall to the GTAO override pass.
 */
export function makePowerLines(opts = {}) {
  const raw = (Array.isArray(opts.points) && opts.points.length >= 2
      ? opts.points : [[-22, 0, 0], [22, 0, 0]]).map(toVec);
  const poleAt = opts.spans > 0 ? resample(raw, Math.round(opts.spans))
                                : raw;
  const spec = Object.assign(
      { height: 8.5, arms: 2, spread: 2.4, radius: 0.15, color: 0x6d6255 },
      (opts.poles && typeof opts.poles === 'object') ? opts.poles : {});
  const rand = mulberry32(Math.round(opts.seed === undefined ? 1
                                                             : opts.seed));
  const arms = Math.max(1, Math.round(spec.arms));
  const perArm = Math.max(1, Math.round(
      spec.wires === undefined ? 3 : spec.wires));
  const radius = Math.max(1e-3,
      (opts.thickness === undefined ? 0.04 : opts.thickness) * 0.5);

  const g = new THREE.Group();
  g.name = 'PowerLines';

  // Crossarm direction at each pole: square to the run, bisected at a
  // corner so a turning line does not tear its own arms apart.
  const side = [];
  for (let i = 0; i < poleAt.length; i++) {
    const a = poleAt[Math.max(0, i - 1)];
    const b = poleAt[Math.min(poleAt.length - 1, i + 1)];
    const d = new THREE.Vector3().subVectors(b, a);
    d.y = 0;
    if (d.lengthSq() < 1e-9) d.set(1, 0, 0);
    d.normalize();
    side.push(new THREE.Vector3().crossVectors(_UP, d).normalize());
  }

  // Where every conductor lands, pole by pole.
  const hooks = poleAt.map((p, i) => {
    const out = [];
    for (let a = 0; a < arms; a++) {
      const y = p.y + spec.height - 0.55 - a * 1.15;
      for (let k = 0; k < perArm; k++) {
        const off = perArm < 2 ? 0
            : (k / (perArm - 1) - 0.5) * spec.spread;
        out.push(new THREE.Vector3(
            p.x + side[i].x * off, y + 0.18, p.z + side[i].z * off));
      }
    }
    return out;
  });

  const spans = [];
  for (let i = 1; i < poleAt.length; i++) {
    spans.push(Math.hypot(poleAt[i].x - poleAt[i - 1].x,
                          poleAt[i].z - poleAt[i - 1].z));
  }
  const mean = spans.reduce((a, b) => a + b, 0) / Math.max(1, spans.length);
  const sag0 = opts.sag === undefined ? mean * 0.04 : opts.sag;

  const strands = [];
  for (let c = 0; c < hooks[0].length; c++) {
    // A lower arm hangs slacker than the one above it, and no two
    // conductors on a line are tensioned identically.
    const slack = (1 + 0.10 * Math.floor(c / perArm))
        * (0.92 + 0.16 * rand());
    for (let i = 1; i < hooks.length; i++) {
      strands.push({
        pts: catenaryPoints(hooks[i - 1][c], hooks[i][c], sag0 * slack,
                            Math.max(8, Math.round(spans[i - 1] * 1.2))),
        phase: rand() * Math.PI * 2,
      });
    }
  }

  const viewH = Math.max(64, opts.viewportHeight === undefined
      ? 576 : opts.viewportHeight);
  const cableMat = makeShaderMaterial({
    name: 'PowerCable',
    uniforms: {
      uCbColor: { value: toColor(opts.color, 0x15181c) },
      uCbRadius: { value: radius },
      uCbMinPx: { value: Math.max(0, opts.minPixels === undefined
          ? 1.5 : opts.minPixels) * 0.5 },
      uCbViewH: { value: viewH },
      uCbSway: { value: Math.max(0, opts.sway === undefined ? 0.05
                                                            : opts.sway) },
    },
    varyings: CABLE_VARYINGS,
    vertexHead: CABLE_VHEAD,
    vertexMain: CABLE_VERTEX,
    fragmentHead: 'uniform vec3 uCbColor;',
    fragmentMain: CABLE_FRAGMENT,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const cables = new THREE.Mesh(cableGeometry(strands), cableMat);
  cables.name = 'Cables';
  cables.renderOrder = 2;
  g.add(keepOutOfDepthPasses(cables));

  if (opts.poles !== false) {
    const wood = [];
    const ceramic = [];
    for (let i = 0; i < poleAt.length; i++) {
      const p = poleAt[i];
      const yaw = Math.atan2(side[i].x, side[i].z);
      const shaft = new THREE.CylinderGeometry(
          spec.radius * 0.72, spec.radius, spec.height, 8);
      shaft.translate(p.x, p.y + spec.height / 2, p.z);
      wood.push(paintPiece(shaft, rand, p.y));
      for (let a = 0; a < arms; a++) {
        const y = p.y + spec.height - 0.55 - a * 1.15;
        wood.push(paintPiece(_box(0.11, 0.16, spec.spread + 0.5,
                                  p.x, y, p.z, yaw), rand, y - 0.4));
        // The knee braces: without them the arm is a stick balanced on
        // a pole, which is the one thing a crossarm never is.
        for (const s of [-1, 1]) {
          const kn = new THREE.BoxGeometry(0.07, 0.93, 0.07);
          kn.rotateX(s * 0.632);
          kn.translate(0, y - 0.38, s * 0.28);
          kn.rotateY(yaw);
          kn.translate(p.x, 0, p.z);
          wood.push(paintPiece(kn, rand, y - 0.9));
        }
      }
      for (const h of hooks[i]) {
        const ins = new THREE.CylinderGeometry(0.075, 0.055, 0.20, 6);
        ins.translate(h.x, h.y - 0.10, h.z);
        // Porcelain weathers too, just far less; the ramp reads as the
        // grime that collects on the skirt of every insulator.
        ceramic.push(paintPiece(ins, rand, h.y - 0.22,
                                { spread: 0.12, tilt: 0.05, damp: 0.22 }));
      }
    }
    const poleMesh = new THREE.Mesh(
        mergeGeometries(wood, false),
        new THREE.MeshStandardMaterial({
          color: spec.color, roughness: 0.92, metalness: 0.02,
          vertexColors: true,
        }));
    poleMesh.name = 'Poles';
    poleMesh.castShadow = true;
    poleMesh.receiveShadow = true;
    g.add(poleMesh);
    const insMesh = new THREE.Mesh(
        mergeGeometries(ceramic, false),
        new THREE.MeshStandardMaterial({
          color: 0x6f7a6a, roughness: 0.35, metalness: 0.0,
          vertexColors: true,
        }));
    insMesh.name = 'Insulators';
    insMesh.castShadow = true;
    g.add(insMesh);
  }

  g.userData.spans = spans;
  g.userData.tick = (t) => tickShaders(g, t);
  g.userData.setViewport = (px) => {
    cableMat.uniforms.uCbViewH.value = Math.max(64, px);
    return g;
  };
  return g;
}


// ------------------------------------------------------------ billboard

const GLOW_FRAGMENT = [
  '  vec2 bbD = abs(vBbUv - 0.5) * 2.0;',
  '  float bbA = (1.0 - smoothstep(0.18, 1.0, bbD.x))',
  '      * (1.0 - smoothstep(0.18, 1.0, bbD.y));',
  // A halo is glow in the AIR round the sign, and the board is opaque:
  // the card must not veil the artwork it sits in front of. Left solid
  // through the middle it clipped the top band of the poster to 1.0 in
  // daylight AND at night — an additive sheet over the face flattens
  // the print and buys nothing without a bloom pass. So it is hollowed
  // out over the board's own outline (`uBbInner`, the face as a
  // fraction of this card) down to `uBbCore`, which only a light box —
  // whose face really does emit — keeps above zero.
  '  float bbOut = max(bbD.x / uBbInner.x, bbD.y / uBbInner.y);',
  '  bbA *= mix(uBbCore, 1.0, smoothstep(0.60, 1.04, bbOut));',
  // Lit from within it is even; lit by lamps on the top rail it is
  // brightest under them and gone at the hoarding's feet.
  '  bbA *= mix(1.0, smoothstep(-0.35, 0.95, vBbUv.y), uBbBias);',
  // Rolled off, not clamped: a saturating halo draws a flat slab with a
  // hard rim, which is the card again.
  '  bbA = 1.0 - exp(-bbA * bbA * uBbGain * 3.4);',
  '  if (bbA < 0.004) discard;',
  // The gradient is metres wide and 8 bits deep, which is exactly where
  // banding rings; a sub-LSB dither costs one hash and removes it.
  '  bbA *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.06;',
  '  gl_FragColor = vec4(uBbColor, bbA);',
].join('\n');

/**
 * The pasted-up sheet: bold blocks, a headline band, paper grain.
 *
 * Three things this refuses, each one measured on a rendered board.
 * The grain was `sin(u * 91.7 + v * 47.3) * 43758.5 % 1`, which is not
 * a hash at those frequencies — it is a low-order beat, and it printed
 * as DIAGONAL STRIPES across the whole poster. It is fBm now. The ink
 * peaked at L 0.92, i.e. an albedo of ~0.9: a sheet of paper is not a
 * light source, and under the gooseneck lamps that white clipped
 * before the grade could touch it, so every colour is held inside the
 * 0.02..0.80 band. And one flat ink over the sheet is the flat-decal
 * tell, so the paper carries ink-density mottle, a bill-poster's PASTE
 * SEAMS (a sheet this size is hung in strips) and sun bleach up the
 * face — hue variance the eye reads as printing rather than as fill.
 *
 * The band is stated in sRGB because that is what a colour texture
 * holds: 0.155..0.80 here is 0.02..0.60 linear, i.e. the albedo band
 * with the top left clear for the lamps to work in.
 */
function posterTexture(color, seed, lit) {
  const key = Math.round(seed);
  const rand = mulberry32(key * 977 + 13);
  const base = toColor(color, 0xc8352f);
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  // Paper white is a WARM off-white, never 1.0 — the ceiling is the top
  // of the albedo band, and `lit` only decides where inside it we sit.
  const CEIL = lit ? 0.80 : 0.74;
  const FLOOR = 0.155;
  const paper = new THREE.Color().setHSL(
      (hsl.h + 0.47) % 1, Math.min(0.16, hsl.s * 0.22 + 0.04), CEIL * 0.94);
  const accent = new THREE.Color().setHSL(
      (hsl.h + 0.47) % 1, Math.min(1, hsl.s * 0.8 + 0.15), CEIL * 0.68);
  const dark = new THREE.Color().setHSL(
      (hsl.h + 0.02) % 1, hsl.s * 0.9, Math.max(0.09, hsl.l * 0.34));
  // Round-robin, not a roll per bar: a poster printed in three inks uses
  // all three. Rolling independently gave sheets that came out as four
  // near-black bars on red — one hue, which is the flat-decal look this
  // is here to avoid.
  const inks = [paper, dark, accent, dark];
  const bars = [];
  for (let i = 0; i < 4; i++) {
    bars.push({ y: 0.10 + rand() * 0.72, h: 0.05 + rand() * 0.13,
                x: 0.04 + rand() * 0.30, w: 0.30 + rand() * 0.60,
                c: inks[(i + Math.round(seed)) % inks.length] });
  }
  // Where the billposter's strips butt up, and how each strip was inked.
  const strips = 2 + Math.round(rand());
  const stripTone = [];
  for (let i = 0; i <= strips; i++) stripTone.push(0.955 + rand() * 0.09);
  const ox = seedOffset(key) * 0.37;
  const tex = noiseDataTexture(128, (u, v) => {
    const c = base.clone();
    for (const b of bars) {
      if (v > b.y && v < b.y + b.h && u > b.x && u < Math.min(1, b.x + b.w)) {
        c.copy(b.c);
      }
    }
    const s = Math.min(strips - 1, Math.floor(u * strips));
    const su = u * strips - s;
    // The seam itself: a hairline of shadow where one sheet laps the next.
    const seam = 1 - 0.14 * (1 - Math.min(1, Math.min(su, 1 - su) * 26));
    // Ink density (broad) and paper grain (fine), both real fBm so the
    // sheet cannot beat against its own sampling.
    const mottle = fbm2(u * 3.3 + ox, v * 3.3, { seed: key + 5 });
    const grain = fbm2(u * 96, v * 96, { octaves: 2, seed: key + 31 });
    const n = stripTone[s] * seam * (1 + 0.16 * mottle + 0.10 * grain);
    // Sun bleach: the top of a hoarding fades first, and it fades WARM.
    const bleach = 0.13 * v * v;
    const tilt = 0.05 * mottle;
    const out = [
      unit(c.r * n * (1 + tilt) + bleach * CEIL * 0.30),
      unit(c.g * n + bleach * CEIL * 0.27),
      unit(c.b * n * (1 - tilt) + bleach * CEIL * 0.22),
    ];
    // The floor is held on LUMINANCE, not per channel: clamping each
    // channel to it turns a dark red ink into a pink-grey, and the point
    // of the floor is that nothing on the sheet is a hole.
    const lum = 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2];
    const k = lum < FLOOR ? FLOOR / Math.max(lum, 1e-4) : 1;
    return out.map((x) => Math.min(CEIL, x * k));
  });
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/**
 * A hoarding or a lit sign panel, on the structure that holds it up.
 *
 * A billboard without its frame is a decal floating in the air, so the
 * legs, the back bracing and the rim come with the face. `kind` picks
 * which of the two real things this is, and they light differently
 * because they ARE lit differently: a `'hoarding'` is a pasted sheet
 * lit from OUTSIDE by gooseneck lamps on the top rail, so the poster is
 * brightest under each lamp and falls off toward its feet; a `'panel'`
 * is a light box lit from WITHIN, so the face is even and the structure
 * around it catches the spill.
 *
 * The light itself is `lib/neon.js`: `patchNeonSpill` already owns
 * windowed falloff, the wrapped terminator a broad source needs, and
 * the reflection through the receiving surface's own albedo, so this
 * hands its sources to that patch instead of growing a second copy. It
 * patches its OWN structure that way at build time and hands the same
 * sources out through `userData.spillSources(n)` — give them to a
 * `patchNeonSpill` on the wall behind or the pavement below, which is
 * the difference between a lit sign and a lit street.
 *
 * @param {object} [opts] `width` / `height` of the face in metres
 *   (default 6 x 3); `kind` `'hoarding'` (default) or `'panel'`;
 *   `color` the poster's dominant ink, which is also what the sign
 *   throws (default 0xc8352f); `lit` (default false) turns on the
 *   lamps or the light box, the halo and the spill; `lift` metres from
 *   the ground to the bottom of the face (default the greater of 2.4
 *   and half the height); `frameColor` (default 0x4a4c4f); `glow` halo
 *   brightness (default 1); `ambient` how bright the surroundings are,
 *   0 an unlit street to 1 open daylight, passed straight to
 *   `patchNeonSpill` (default 0.15); `seed` the poster and the lamp
 *   spacing (default 1).
 * @returns {THREE.Group} Named `Billboard`, resting at y = 0 and facing
 *   +Z (`userData.forward`), with `userData.spillSources(n)` (world
 *   points, read through the group's CURRENT matrix),
 *   `userData.relight()` — call it after MOVING a lit sign, since the
 *   spill on its own structure was aimed where it was built — and
 *   `userData.tick(t)`. The halo is guarded by `keepOutOfDepthPasses`.
 */
export function makeBillboard(opts = {}) {
  const w = Math.max(0.5, opts.width === undefined ? 6 : opts.width);
  const h = Math.max(0.4, opts.height === undefined ? 3 : opts.height);
  const panelKind = opts.kind === 'panel' ? 'panel' : 'hoarding';
  const lit = !!opts.lit;
  const lift = opts.lift === undefined ? Math.max(2.4, h * 0.5) : opts.lift;
  const color = toColor(opts.color, 0xc8352f);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const rand = mulberry32(Math.round(seed) * 131 + 7);
  const ambient = unit(opts.ambient === undefined ? 0.15 : opts.ambient);
  const y0 = lift + h / 2;

  const g = new THREE.Group();
  g.name = 'Billboard';

  const frameMat = new THREE.MeshStandardMaterial({
    color: opts.frameColor === undefined ? 0x4a4c4f : opts.frameColor,
    roughness: 0.62, metalness: 0.55,
  });
  const boardMat = new THREE.MeshStandardMaterial({
    color: 0x2b2c2e, roughness: 0.85, metalness: 0.1,
  });
  const faceMat = new THREE.MeshStandardMaterial({
    map: posterTexture(color, seed, lit),
    roughness: panelKind === 'panel' ? 0.28 : 0.78,
    metalness: 0,
  });
  if (lit && panelKind === 'panel') {
    // A light box is lit from within: emissive is the only term that
    // survives an unlit street, and the map keeps the artwork in it.
    faceMat.emissive = new THREE.Color(0xffffff);
    faceMat.emissiveMap = faceMat.map;
    faceMat.emissiveIntensity = 1.5 * (1 - 0.7 * ambient);
  }

  const board = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.14), boardMat);
  board.position.set(0, y0, -0.02);
  board.name = 'Board';
  board.castShadow = true;
  board.receiveShadow = true;
  g.add(board);

  const face = new THREE.Mesh(
      new THREE.PlaneGeometry(w - 0.18, h - 0.18), faceMat);
  face.position.set(0, y0, 0.06);
  face.name = 'Face';
  face.receiveShadow = true;
  g.add(face);

  // Legs, rim and back bracing in one mesh: a billboard is a steel
  // frame with a sheet on it, and the frame is most of the silhouette.
  const steel = [];
  const legX = w * 0.3;
  for (const sx of [-legX, legX]) {
    steel.push(_box(0.18, y0 + h / 2, 0.18, sx, (y0 + h / 2) / 2, -0.16));
    // The raking strut every real hoarding has behind it, lifted by
    // its own half-thickness so the foot rests ON the ground.
    const ang = Math.atan2(lift * 0.75, lift);
    const rake = new THREE.BoxGeometry(0.12, Math.hypot(lift, lift * 0.75),
                                       0.12);
    rake.rotateX(-ang);
    rake.translate(sx, lift * 0.5 + 0.06 * Math.sin(ang),
                   -0.16 - lift * 0.375);
    steel.push(rake);
  }
  steel.push(_box(w + 0.24, 0.16, 0.16, 0, y0 + h / 2 + 0.08, 0));
  steel.push(_box(w + 0.24, 0.16, 0.16, 0, y0 - h / 2 - 0.08, 0));
  steel.push(_box(0.16, h, 0.16, -w / 2 - 0.08, y0, 0));
  steel.push(_box(0.16, h, 0.16, w / 2 + 0.08, y0, 0));

  const lamps = [];
  const sources = [];
  if (lit && panelKind === 'hoarding') {
    const n = Math.max(2, Math.round(w / 3));
    for (let i = 0; i < n; i++) {
      const x = (i + 0.5) / n * w - w / 2 + (rand() - 0.5) * 0.1;
      const ly = y0 + h / 2 + 0.62;
      steel.push(_box(0.09, 0.09, 0.9, x, ly, 0.42));
      steel.push(_box(0.09, 0.7, 0.09, x, ly - 0.35, 0.86));
      const head = new THREE.CylinderGeometry(0.17, 0.11, 0.2, 10);
      head.rotateX(Math.PI * 0.42);
      head.translate(x, ly - 0.62, 0.84);
      lamps.push(head);
      sources.push({ position: new THREE.Vector3(x, ly - 0.72, 0.78),
                     color: new THREE.Color(0xfff0cf) });
    }
  }

  const frame = new THREE.Mesh(mergeGeometries(steel, false), frameMat);
  frame.name = 'Frame';
  frame.castShadow = true;
  frame.receiveShadow = true;
  g.add(frame);

  if (lamps.length) {
    const lampMat = new THREE.MeshStandardMaterial({
      color: 0xd8d2c4, roughness: 0.4, metalness: 0.6,
      emissive: new THREE.Color(0xfff0cf),
      emissiveIntensity: 2.2 * (1 - 0.7 * ambient),
    });
    const lampMesh = new THREE.Mesh(mergeGeometries(lamps, false), lampMat);
    lampMesh.name = 'Lamps';
    g.add(lampMesh);
  }
  if (lit && panelKind === 'panel') {
    for (let i = 0; i < 4; i++) {
      sources.push({
        position: new THREE.Vector3(
            (i / 3 - 0.5) * w * 0.8, y0 + (rand() - 0.5) * h * 0.5, 0.1),
        color: color.clone(),
      });
    }
  }

  if (lit) {
    // The card carries the halo OUTSIDE the board now, so it has to be
    // big enough to hold one: at 1.5 x 1.7 the aureole was a bright rim
    // a few pixels wide instead of light standing in the air.
    const CARD = [2.0, 2.2];
    const glow = new THREE.Mesh(
        new THREE.PlaneGeometry(w * CARD[0], h * CARD[1]),
        makeShaderMaterial({
          name: 'BillboardGlow',
          uniforms: {
            uBbColor: { value: panelKind === 'panel'
                ? color.clone().lerp(new THREE.Color(0xffffff), 0.35)
                : new THREE.Color(0xffe9c4) },
            // A halo is light scattered in the AIR, so it is only ever
            // visible against a darker frame: linear in `ambient` it
            // still clipped the sky round a hoarding at midday
            // (blown_frac 0.033 in the day close view), and the curve is
            // what makes one card read at night and vanish by noon.
            uBbGain: { value: Math.max(0, opts.glow === undefined ? 1
                                                                 : opts.glow)
                * Math.pow(Math.max(0, 1 - ambient), 1.7) },
            uBbBias: { value: panelKind === 'panel' ? 0 : 1 },
            // Where the board's own outline falls on this card.
            uBbInner: { value: new THREE.Vector2(1 / CARD[0], 1 / CARD[1]) },
            uBbCore: { value: panelKind === 'panel' ? 0.30 : 0.0 },
          },
          varyings: 'varying vec2 vBbUv;',
          vertexMain: '  vBbUv = uv;',
          fragmentHead: 'uniform vec3 uBbColor;\nuniform float uBbGain;\n'
              + 'uniform float uBbBias;\nuniform vec2 uBbInner;\n'
              + 'uniform float uBbCore;',
          fragmentMain: GLOW_FRAGMENT,
          additive: true,
        }));
    glow.position.set(0, y0, 0.09);
    glow.name = 'Glow';
    glow.renderOrder = 3;
    g.add(keepOutOfDepthPasses(glow));
  }

  // Sources are read through the CURRENT world matrix, so placing the
  // sign moves the light it throws.
  g.userData.spillSources = (n) => {
    g.updateWorldMatrix(true, false);
    const want = Math.min(8, Math.max(1, Math.round(
        n === undefined ? sources.length || 1 : n)));
    const pick = want >= sources.length
        ? sources
        : sources.filter((_, i) => i % Math.ceil(sources.length / want) === 0)
            .slice(0, want);
    return pick.map((s) => ({
      position: s.position.clone().applyMatrix4(g.matrixWorld),
      color: s.color.clone(),
    }));
  };
  // The sign lights its OWN structure through neon.js rather than
  // growing a second spill; the face too, when lamps light it.
  g.userData.relight = () => {
    if (!sources.length) return g;
    const list = g.userData.spillSources(sources.length);
    const reach = Math.max(w, h) * 1.4;
    // Measured at night: a strength of 1.1 on the steel and 1.5 on the
    // poster clipped both — the frame went to a white silhouette and the
    // top third of the artwork disappeared into the wash. A gooseneck
    // makes POOLS on a sheet, so the spill has to stay under the ink it
    // is lighting.
    patchNeonSpill(frameMat, { sources: list, radius: reach,
                               strength: 0.72, ambient });
    if (panelKind === 'hoarding') {
      patchNeonSpill(faceMat, { sources: list, radius: reach,
                                strength: 0.85, ambient });
    }
    return g;
  };
  g.userData.relight();
  g.userData.tick = (t) => tickShaders(g, t);
  g.userData.forward = '+Z';
  return g;
}
