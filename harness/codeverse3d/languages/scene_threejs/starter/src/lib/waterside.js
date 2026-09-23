/**
 * Waterlines: the contact transitions where ground, rock and water meet.
 *
 * A bank that meets water at a flat cut reads as two slabs intersecting,
 * and it is the cheapest realism there is to fix. All three patches here
 * go through `patchStandard`, so each material keeps its lighting,
 * shadows, fog and depth chunks, and they CHAIN — a shore ground
 * normally wears `patchShoreWet` and `patchShoreFoam` at once. Every
 * tunable is a uniform, so two materials wearing one patch share one
 * compiled program while keeping their own values.
 *
 * They complement `water.js` instead of fighting it: the addon Water is
 * a reflective (RTT) surface and its raw ShaderMaterial has no
 * `<color_fragment>` hook, so `patchShallowWater` belongs on a plain
 * standard-material reach (a shallow bay, a river) beside it.
 */

import * as THREE from 'three';
import {
  composeRoughness, toColor, unit, withBase, worldBase,
} from './shader.js';

// One base for all three, on the world varyings terrain_shade shares, so
// a bank wearing a splat and a waterline declares ONE pair.
const BASE = worldBase('waterside:base', 'wsP', 'wsN');

/**
 * Darken and gloss the ground below a waterline, fading out above it.
 *
 * Wet ground is darker than dry ground — that is the whole cue, and
 * without it a bank meets the water as a flat cut with no contact at
 * all. The fade band above the line is what makes it a shore rather
 * than a painted stripe, and the line itself is noise-wandered because
 * a level contour is a ruled edge no bank has. Below the line the
 * surface stays fully wet, so submerged ground reads as submerged.
 *
 * Wetting is not a grey multiply. A water film drops the albedo AND
 * deepens the colour already there, then gleams at grazing angles — so
 * the band carries all three: darken, saturate, and a fresnel sheen in
 * the SKY's hue (`fogColor` where the scene is fogged, which is the one
 * sky-family colour a `<color_fragment>` patch can reach), which is
 * what separates wet sand from sand in shadow.
 *
 * `gloss` lands on the MATERIAL, not per pixel: this patch writes only
 * at `<color_fragment>`, BEFORE `<roughnessmap_fragment>` declares
 * `roughnessFactor`, and does not use `patchStandard`'s `roughnessBody`.
 * It therefore defaults to a LIGHT touch:
 * a shore ground is mostly dry, and the reference default of 0.45 took
 * `MAT.soil()` from roughness 0.95 to 0.43 over the whole beach —
 * measured on this renderer (ACES, exposure 1.0, baked environment) it
 * turned the dry sand into satin and pushed the frame to mean_lum 0.85.
 * The per-pixel sheen above is what the wet band actually reads from;
 * pass `gloss: 0.45` when the bank has a material of its own.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place.
 * @param {object} [opts] `level` world Y of the water surface (default
 *   0); `band` metres of fade ABOVE it (default 0.25); `darken` how far
 *   the albedo drops at full wet, 0..1 (default 0.45); `saturate` extra
 *   chroma at full wet (default 0.35); `sheen` grazing-angle lift in
 *   the sky's hue (default 0.22); `gloss` multiplier on the material's
 *   dry roughness (default 0.85).
 * @returns {THREE.Material} The same material. Its uniforms stay live on
 *   `material.userData.uniforms`, so `uWetY` can follow a tide.
 */
export function patchShoreWet(material, opts = {}) {
  const level = opts.level === undefined ? 0 : opts.level;
  const band = opts.band === undefined ? 0.25 : opts.band;
  const darken = opts.darken === undefined ? 0.45 : opts.darken;
  const gloss = opts.gloss === undefined ? 0.85 : opts.gloss;
  const sat = opts.saturate === undefined ? 0.35 : opts.saturate;
  const sheen = opts.sheen === undefined ? 0.22 : opts.sheen;
  composeRoughness(material, 'waterside:wet', Math.max(gloss, 0));
  return withBase(material, BASE, {
    name: 'waterside:shoreWet',
    uniforms: {
      uWetY: { value: level },
      uWetBand: { value: Math.max(band, 1e-3) },
      uWetDark: { value: unit(darken) },
      uWetSat: { value: Math.max(sat, 0) },
      uWetSheen: { value: Math.max(sheen, 0) },
    },
    fragmentHead: [
      'uniform float uWetY;',
      'uniform float uWetBand;',
      'uniform float uWetDark;',
      'uniform float uWetSat;',
      'uniform float uWetSheen;',
    ].join('\n'),
    fragmentBody: [
      '  float wtH = vAstraWorld.y - uWetY;',
      // astraFbm2 over 3 octaves averages ~0.44; centred there, this
      // wanders the tide mark by up to half a band either way.
      '  wtH += (astraFbm2(vAstraWorld.xz * 0.7, 3) - 0.44) * uWetBand;',
      '  float wtK = astraContact(wtH, uWetBand);',
      '  vec3 wtDry = diffuseColor.rgb;',
      '  float wtL = dot(wtDry, vec3(0.2126, 0.7152, 0.0722));',
      '  vec3 wtWet = mix(vec3(wtL), wtDry, 1.0 + uWetSat)',
      '               * (1.0 - uWetDark);',
      '  diffuseColor.rgb = mix(wtDry, max(wtWet, vec3(0.0)), wtK);',
      // The sheen the material-wide gloss cannot place: it belongs on
      // the film, not on the dry beach behind it. cameraPosition is a
      // three built-in in both stages, so no extra varying is needed.
      '  vec3 wtSky = vec3(0.42, 0.50, 0.60);',
      '#ifdef USE_FOG',
      '  wtSky = fogColor;',
      '#endif',
      '  vec3 wtV = normalize(cameraPosition - vAstraWorld);',
      '  float wtF = astraFresnel(vAstraWorldN, wtV, 4.0);',
      '  diffuseColor.rgb += wtSky * (wtF * wtK * uWetSheen);',
    ].join('\n'),
  });
}

/**
 * A broken white band where water meets anything, torn into streamers.
 *
 * Foam is the second contact cue and it works on ground, rock or a
 * jetty leg alike. It is strongest AT the waterline and hands over to
 * both neighbours — fading up the bank and down under the surface —
 * rather than ending at an edge. The tear runs ALONG the shore because
 * the waves run across it: the horizontal part of the world normal
 * points up-slope, so its perpendicular is the shore direction, and the
 * noise is stretched sevenfold along it into streamers instead of the
 * bands-marching-at-the-viewer a ranked field would give. On a dead
 * flat surface there is no shore direction to find and it falls back to
 * the world X axis.
 *
 * The band breathes: two decorrelated swells move the LINE itself, so
 * the foam runs up the bank and drains again. Drive it with
 * `tickShaders(scene, t)` — an un-advanced uTime is a frozen band.
 *
 * Foam is bubbles, never poured paint, so the band is broken TWICE: the
 * streamer field tears it along the shore and a fine lace field breaks
 * the churn inside it. And it is not one flat white — the thin film at
 * the tail keeps a good part of the wet colour under it while only the
 * churn goes bright, and the whole band carries a hue break, because a
 * single white is the giveaway of a painted waterline. The default
 * white is graded to 0.74-0.79 linear so it lifts under bloom instead
 * of clipping (a 0xeef4f5 foam runs 0.87 in blue and blows first).
 *
 * A band in world Y covers `band / slope` metres of GROUND, so the
 * flatter the surface the wider it spreads — and on the one surface
 * that is exactly level, a water plane, it spreads over the whole
 * reach: measured here, a foam patch on a flat reach tinted every
 * fragment of it (contact 0.59 at t = 1.5) and laid streamers out to
 * the horizon. So the band fades out once its own footprint passes
 * `reach` metres, which leaves the bank, the rocks and the piles
 * untouched and takes the wash off the open water. A flat reach has no
 * waterline of its own; the foam belongs on what the water MEETS.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place.
 * @param {object} [opts] `level` world Y of the water surface (default
 *   0); `band` metres the foam reaches EITHER side of it (default
 *   0.35); `color` THREE.Color or hex (default a cold white); `speed`
 *   swell rate in radians per second (default 0.6); `strength` peak
 *   coverage, 0..1 (default 0.85); `reach` metres of ground the band
 *   may cover before it fades out (default 3, gone by 2.5x that; pass
 *   a huge value to put the band back on level surfaces).
 * @returns {THREE.Material} The same material, with its uniforms live on
 *   `material.userData.uniforms`.
 */
export function patchShoreFoam(material, opts = {}) {
  const level = opts.level === undefined ? 0 : opts.level;
  const band = opts.band === undefined ? 0.35 : opts.band;
  const speed = opts.speed === undefined ? 0.6 : opts.speed;
  const strength = opts.strength === undefined ? 0.85 : opts.strength;
  const reach = opts.reach === undefined ? 3 : opts.reach;
  return withBase(material, BASE, {
    name: 'waterside:shoreFoam',
    uniforms: {
      uFoamY: { value: level },
      uFoamBand: { value: Math.max(band, 1e-3) },
      uFoamColor: { value: toColor(opts.color, 0xdfe6e4) },
      uFoamSpeed: { value: speed },
      uFoamAmt: { value: unit(strength) },
      uFoamReach: { value: Math.max(reach, 1e-3) },
    },
    fragmentHead: [
      'uniform float uFoamY;',
      'uniform float uFoamBand;',
      'uniform vec3 uFoamColor;',
      'uniform float uFoamSpeed;',
      'uniform float uFoamAmt;',
      'uniform float uFoamReach;',
    ].join('\n'),
    fragmentBody: [
      '  float fmT = uTime * uFoamSpeed;',
      // Two swells, not one: a single sine is a metronome, and it is
      // the LINE that moves, so the band runs up and drains.
      '  float fmS = sin(fmT) * 0.35 + sin(fmT * 0.63 + 1.7) * 0.22;',
      '  float fmH = vAstraWorld.y - uFoamY - fmS * uFoamBand;',
      // The shore frame: a bank tilts its normal up-slope, so the
      // horizontal normal is ACROSS the shore and its perpendicular
      // runs along it. Dead flat, there is no shore — use +X.
      '  vec2 fmNx = vAstraWorldN.xz;',
      '  float fmL = length(fmNx);',
      '  vec2 fmA = fmL > 1e-3 ? fmNx / fmL : vec2(1.0, 0.0);',
      '  vec2 fmB = vec2(-fmA.y, fmA.x);',
      '  float fmU = dot(vAstraWorld.xz, fmB);',
      '  float fmV = dot(vAstraWorld.xz, fmA);',
      // Structure runs ACROSS the flow: waves run up the bank, so the
      // tear varies slowly along the shore and fast across it.
      '  float fmN = astraFbm2(vec2(fmU * 0.30 + fmT * 0.05,',
      '                             fmV * 2.10 - fmT * 0.35), 3);',
      // The lace: a second, much finer field across the same frame,
      // drifting faster. Without it the streamers read as one poured
      // sheet with a torn outline instead of as bubbles.
      '  float fmD = astraFbm2(vec2(fmU * 1.70,',
      '                             fmV * 6.40 - fmT * 0.60), 2);',
      '  float fmK = astraContact(abs(fmH), uFoamBand);',
      '  fmK *= smoothstep(0.24, 0.62, fmN);',
      '  fmK *= 0.55 + 0.45 * smoothstep(0.18, 0.72, fmD);',
      // How much GROUND this band covers: a rise of uFoamBand runs
      // band * Ny / |Nxz| metres along a surface of that slope, and
      // 1/0 on a level one — where the band would be the whole reach.
      '  float fmSpan = uFoamBand * abs(vAstraWorldN.y) / max(fmL, 1e-4);',
      '  fmK *= 1.0 - smoothstep(uFoamReach, uFoamReach * 2.5, fmSpan);',
      // The tail is a thin film that still shows what is under it; only
      // the churn goes bright. Then a hue break, so no two square
      // metres of the band are the same white.
      '  vec3 fmC = mix(uFoamColor * 0.70, uFoamColor,',
      '                 smoothstep(0.30, 0.88, fmN));',
      '  fmC = astraHueBreak(fmC, vAstraWorld.xz, 1.7, 0.14);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, fmC,',
      '                         clamp(fmK * uFoamAmt, 0.0, 1.0));',
    ].join('\n'),
  });
}

// Odd, so the grid is symmetric about the centre of `bounds` and the
// least-squares normal equations decouple into three sums.
const BED_SAMPLES = 9;

/**
 * Fit `bed(x, z) = c0 + cx*x + cz*z` to `bedAt` over `bounds`.
 *
 * Least squares on a grid symmetric about its own centre, where the
 * cross terms vanish, so no matrix solve is needed.
 */
function fitBedPlane(bedAt, bounds, level) {
  const flat = new THREE.Vector3(level, 0, 0);
  if (typeof bedAt !== 'function') return flat;
  const [x0, z0, x1, z1] = bounds;
  const xc = (x0 + x1) / 2;
  const zc = (z0 + z1) / 2;
  const n = BED_SAMPLES;
  let sb = 0, sx = 0, sz = 0, sxx = 0, szz = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const x = x0 + (x1 - x0) * (i / (n - 1));
      const z = z0 + (z1 - z0) * (j / (n - 1));
      const b = bedAt(x, z);
      if (!Number.isFinite(b)) return flat;
      sb += b;
      sx += (x - xc) * b;
      sz += (z - zc) * b;
      sxx += (x - xc) * (x - xc);
      szz += (z - zc) * (z - zc);
    }
  }
  const cx = sxx > 1e-9 ? sx / sxx : 0;
  const cz = szz > 1e-9 ? sz / szz : 0;
  return new THREE.Vector3(sb / (n * n) - cx * xc - cz * zc, cx, cz);
}

/**
 * Colour a water surface by how deep it runs, so a reach is not a slab.
 *
 * One flat colour over a whole reach is what makes water read as a
 * sheet of plastic laid on the ground; real water goes pale and warm
 * where it runs thin over the bank and saturates into the deep tone
 * where the bed drops away, which is also what tells the eye where the
 * bank IS. The shallow edge is noise-wandered so the isoline does not
 * rule a contour across the surface.
 *
 * DEPTH ROUTE — the bed comes from world XZ through a tilted plane
 * least-squares FITTED to `bedAt` on the CPU, and depth is this
 * fragment's own world Y minus that plane. That is the one of the two
 * that a fragment shader can actually run: world Y against a `bedLevel`
 * is a single number over a flat water plane and would paint the whole
 * reach one colour, while a true per-fragment `bedAt` needs the
 * heightfield uploaded as a texture or a depth prepass, and this engine
 * runs neither. A plane is exact for a bed that slopes, which is what a
 * bank is; with no `bedAt` it degenerates to the constant `bedLevel`.
 *
 * Not for the addon Water from `water.js` — that is a raw
 * ShaderMaterial with no `<color_fragment>` hook (and a reflective RTT
 * surface). Use this on a plain standard-material reach beside it.
 *
 * @param {THREE.Material} material The water surface's built-in
 *   material, patched in place.
 * @param {object} [opts] `bedAt` function(x, z) returning the bed's
 *   world Y, sampled 9x9 over `bounds` and fitted; `bounds`
 *   [minX, minZ, maxX, maxZ] to fit over (default 120 m about the
 *   origin); `bedLevel` the constant bed used when there is no `bedAt`
 *   (default -2, two metres under a surface at y = 0); `deep` /
 *   `shallow` THREE.Color or hex — the deep end defaults to water.js's
 *   own `waterColor`, so a reach agrees with the scene's RTT ocean, and
 *   the shallow end to a muted jade (0x6fa392, ~0.27 linear; the pale
 *   sage it replaced ran 0.40-0.57 and came back off this renderer as
 *   white paint); `range` metres of depth over which shallow becomes
 *   deep (default 2); `edgeFade` how much of the material's own opacity
 *   survives in the shallows, so the bed shows through where the water
 *   runs thin (default 0.55 on a transparent material, 1 on an opaque
 *   one, where alpha is ignored anyway).
 * @returns {THREE.Material} The same material, with its uniforms live on
 *   `material.userData.uniforms`.
 */
export function patchShallowWater(material, opts = {}) {
  const bedLevel = opts.bedLevel === undefined ? -2 : opts.bedLevel;
  const bounds = opts.bounds || [-60, -60, 60, 60];
  const range = opts.range === undefined ? 2 : opts.range;
  // A UNIFORM, never a second source variant: the cache key names the
  // patch, so the first material to compile decides the GLSL for every
  // material wearing this chain. Opaque materials get 1 — three
  // discards alpha there, and a fade they cannot show is a lie in the
  // uniform dump.
  const fade = opts.edgeFade === undefined
      ? (material && material.transparent ? 0.55 : 1)
      : unit(opts.edgeFade);
  return withBase(material, BASE, {
    name: 'waterside:shallow',
    uniforms: {
      uShoalBed: { value: fitBedPlane(opts.bedAt, bounds, bedLevel) },
      uShoalDeep: { value: toColor(opts.deep, 0x0e3f5c) },
      uShoalShallow: { value: toColor(opts.shallow, 0x6fa392) },
      uShoalRange: { value: Math.max(range, 1e-3) },
      uShoalFade: { value: fade },
    },
    fragmentHead: [
      'uniform vec3 uShoalBed;',
      'uniform vec3 uShoalDeep;',
      'uniform vec3 uShoalShallow;',
      'uniform float uShoalRange;',
      'uniform float uShoalFade;',
    ].join('\n'),
    fragmentBody: [
      '  float swBed = uShoalBed.x + uShoalBed.y * vAstraWorld.x',
      '              + uShoalBed.z * vAstraWorld.z;',
      '  float swD = vAstraWorld.y - swBed;',
      // The shoal edge wanders by a third of the range, or the ramp
      // draws a contour line across open water.
      '  swD += (astraFbm2(vAstraWorld.xz * 0.35, 3) - 0.44)',
      '         * uShoalRange * 0.7;',
      '  float swK = smoothstep(0.0, uShoalRange, max(swD, 0.0));',
      '  vec3 swC = mix(uShoalShallow, uShoalDeep, swK);',
      // Silt, weed and bed patches move the hue at metre scale, and
      // they do it in the SHALLOWS, where the bed shows through; the
      // deep end is the colour of water and holds still. Without this
      // the reach is one clean ramp — the tell of painted water.
      '  swC = astraHueBreak(swC, vAstraWorld.xz, 0.09,',
      '                      0.34 * (1.0 - swK * 0.8));',
      '  diffuseColor.rgb = swC;',
      // Thin water is see-through water: the bed under the shoal edge
      // is the cue that says where the bank goes.
      '  diffuseColor.a *= mix(uShoalFade, 1.0, swK);',
    ].join('\n'),
  });
}
