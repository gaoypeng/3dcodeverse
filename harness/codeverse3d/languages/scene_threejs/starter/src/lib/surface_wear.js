/**
 * Surface wear: the two passes that land on EVERY material in a scene.
 *
 * The rest of this library shades one KIND of thing — a cliff, a leaf,
 * a waterline. These two shade whatever they are handed, which is why
 * they are the cheapest realism here: a scene reads as CG largely
 * because every material paints one exact colour at one exact gloss
 * over a whole object, and no photographed surface does either. Both go
 * through `patchStandard`, so lighting, shadows, fog and the depth
 * chunks survive; both shade from world position and world normal, so
 * neither needs a UV; and both CHAIN with each other and with
 * `terrain_shade` / `waterside` on one material.
 *
 * The same local fields modify roughness after the roughness map: clean
 * areas retain their authored finish and worn edges alone become polished.
 *
 * PORT NOTES (2026-09-01, measured on our host — no post chain, ACES in
 * the fragment tail, a bright baked environment as the specular source):
 *
 *  1. The breakup's hue jitter was `astraHueShift`, a rotation about the
 *     grey axis, and that rotation returns a NEUTRAL unchanged — exactly
 *     zero on a grey, 0.0014 peak channel move on our stone albedo
 *     0x8d8577 at the default 0.03. Stone, plaster, concrete and dirt
 *     are most of what this patch ever lands on, so the one line meant
 *     to make it "a change of MATERIAL rather than a brightness dial"
 *     was a no-op on them. It is a warm/cool break now — the swing
 *     daylight actually makes, and the same transfer `astraHueBreak`
 *     uses — so `hue` is a fraction of the albedo, not radians.
 *  2. Edge wear degenerated into a barber pole on anything thin. Past
 *     ~3x the onset curvature the ramp is saturated, so on a 6 cm rail
 *     post the ONLY structure left was one 14 cm blotch field on a 12 cm
 *     object: alternating solid bands of flat bone white. The break-up
 *     is two octaves now, both scaled to the width asked for, and the
 *     worn tone is thinned paint before it is bare substrate.
 *  3. The worn tone was one flat colour over every material wearing it.
 *     It carries its own value and warm/cool variance now, and the
 *     default substrate came down from 0xe8e2d6 (0.816 linear — over
 *     the top of the sane albedo range before the value break, and it
 *     read as white primer under our sun) to 0xd2cabb, which leaves the
 *     break room to peak at 0.79.
 *  4. Edge gloss was an area-weighted error: a whole material polished
 *     by 0.35 * strength for wear that covers a fraction of it. 0.12.
 */

import {
  patchStandard, glslAxes, glslCurv, glslTriNoise, seedVec3,
  toColor, unit, worldBase,
} from './shader.js';

// Both patches read the same two fields, so they are built once. A
// planar UV would smear either of them into vertical streaks on a wall,
// and these patches land on walls.
const WEAR_HEAD = [
  glslAxes('astraWearAxes'),
  glslTriNoise('astraWearNoise', 13.7, 41.3, 71.9),
  glslCurv('astraWearCurv'),
].join('\n');

// On the world varyings terrain_shade and waterside share, so a material
// wearing a splat, a waterline and wear declares ONE pair; the locals
// are prefixed wr because those two vertex bodies own astraWp and wsP.
const BASE = worldBase('wear:base', 'wrP', 'wrN', WEAR_HEAD);

const seedOffset = (seed) => seedVec3(seed, 0.17, 3.71, 7.13, 64);

/**
 * Break up the flat, even surface that reads as moulded plastic.
 *
 * A built-in material paints one exact colour over a whole object and
 * nothing photographed is one exact colour — that evenness is most of
 * what "CG" means, and it costs one patch on every material to lose.
 * This lays a faint world-space variation over the albedo: a slow
 * blotch at `scale`, a mid field at half of it, a grain at a quarter,
 * and a warm/cool break so it is a change of MATERIAL rather than a
 * brightness dial. Every field is projected on the three world axes and
 * blended by the world normal, so a wall gets grain instead of vertical
 * streaks and two objects sharing a scale share a surface.
 *
 * Keep it under the threshold of "what is that": the test of a good
 * breakup is that it cannot be named in a still, only missed when it is
 * taken away. Past ~0.2 `strength` it stops being texture and reads as
 * dirt, and the grain fades itself out once a pixel spans a cycle of it
 * — beyond that it is no longer texture but static, which is the one
 * way this reads as an effect rather than as a surface.
 *
 * Roughness follows the seeded breakup per pixel, breaking uniform
 * highlights without changing the material's authored base roughness.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` patches every mesh
 *   wearing it, so clone it first if that is not what you want.
 * @param {object} [opts] `scale` metres per cycle of the blotch
 *   (default 0.6); `strength` peak albedo swing either way, 0..1
 *   (default 0.10); `hue` the warm/cool break, as a fraction of the
 *   albedo either way (default 0.15 — NOT radians, see the port note;
 *   it is the peak, and the field reaches about 0.6 of it, which puts
 *   the typical swing on `astraHueBreak`'s own calibrated stone number);
 *   `seed` decorrelates two materials and picks the roughness detune
 *   (default 1).
 * @returns {THREE.Material} The same material. Its uniforms stay live
 *   on `material.userData.uniforms` for a scene that wants to retune
 *   them.
 */
export function patchMicroBreakup(material, opts = {}) {
  const scale = opts.scale === undefined ? 0.6 : opts.scale;
  const strength = opts.strength === undefined ? 0.10 : opts.strength;
  const hue = opts.hue === undefined ? 0.15 : opts.hue;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'wear:micro',
    uniforms: {
      uMicroScale: { value: Math.max(1e-3, scale) },
      uMicroAmt: { value: unit(strength) },
      uMicroHue: { value: hue },
      uMicroSeed: { value: seedOffset(seed) },
    },
    fragmentHead: [
      'uniform float uMicroScale;',
      'uniform float uMicroAmt;',
      'uniform float uMicroHue;',
      'uniform vec3 uMicroSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec3 mbW = astraWearAxes(normalize(vAstraWorldN));',
      '  vec3 mbP = vAstraWorld / uMicroScale + uMicroSeed;',
      '  float mbLo = astraWearNoise(mbP, mbW);',
      '  float mbMi = astraWearNoise(mbP * 2.03, mbW);',
      '  float mbHi = astraWearNoise(mbP * 4.1, mbW);',
      // Each field is dropped once a pixel spans most of a cycle of it:
      // past that it is no longer texture, it is static, and static is
      // the one way this reads as an effect. Two octaves need two
      // fades, or the mid one aliases across a floor at grazing angle
      // while the fine one is already gone.
      '  float mbFw = length(fwidth(mbP));',
      // Filter each field toward its mean BEFORE either brightness or
      // hue reads it. Filtering only brightness left coloured static at
      // grazing angles, and even the coarsest octave aliases far away.
      '  mbLo = mix(0.5, mbLo, 1.0 - smoothstep(0.35, 1.00, mbFw));',
      '  mbMi = mix(0.5, mbMi, 1.0 - smoothstep(0.35, 1.00, mbFw * 2.03));',
      '  mbHi = mix(0.5, mbHi, 1.0 - smoothstep(0.35, 1.00, mbFw * 4.1));',
      // Value noise clusters around 0.5, so it is centred and stretched
      // to reach the strength asked for; three octaves rather than two
      // because a surface with one blotch size and one grain size is
      // still a pattern, and the clamp keeps `strength` a hard bound
      // however they stack.
      '  float mbV = clamp((mbLo - 0.5) * 2.4',
      '                    + (mbMi - 0.5) * 1.5',
      '                    + (mbHi - 0.5) * 1.6, -1.0, 1.0);',
      // WARM/COOL, not a hue rotation: rotating about the grey axis
      // leaves a neutral exactly unchanged, and stone, plaster and
      // concrete are most of what this rides on. Warm where the sun has
      // dried and bleached it, cool where only the sky reaches. It
      // rides the DIFFERENCE of two fields, which is not the brightness
      // field again.
      '  float mbT = clamp((mbLo - mbHi) * 2.2, -1.0, 1.0) * uMicroHue;',
      '  diffuseColor.rgb *= vec3(1.0 + mbT, 1.0 + mbT * 0.15,',
      '                           1.0 - mbT);',
      // The dark half of the field is dust and grime, and dust is
      // greyer than what it settles on: saturation varies with it, so
      // the patch is a change of material and not one brightness dial.
      '  float mbL = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(mbL),',
      '                         max(-mbV, 0.0) * uMicroAmt * 0.6);',
      '  diffuseColor.rgb *= 1.0 + mbV * uMicroAmt;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = clamp(roughnessFactor * (1.0 - mbV * uMicroAmt * 0.5), 0.04, 1.0);',
  });
}

/**
 * Lighten the convex edges, where hands and weather take the finish off.
 *
 * Paint wears off what sticks out. An object whose edges match its
 * faces exactly has never been touched, and giving the edges back their
 * lighter, harder-worn tone is the second thing (after breaking the
 * flat colour) that moves a surface from moulded to used.
 *
 * EDGE ROUTE — the world normal's screen-space DERIVATIVE divided by
 * the world-space length of that same pixel step. The ratio is the
 * surface's curvature in 1/m, which is the one edge estimate a fragment
 * can compute: it holds still under distance and resolution (a raw
 * `fwidth(normal)` would draw a fixed-pixel-width line that thickens as
 * the camera pulls back), and it is SIGNED, so a concave corner — where
 * dirt collects rather than wear — is left alone.
 *
 * It reads ZERO across a hard, unwelded edge (`BoxGeometry`'s corners,
 * anything flat-shaded): derivatives never cross a primitive, so both
 * faces see a constant normal right up to the seam. That is also why
 * this can never flicker into a wireframe. Bevel or smooth-shade the
 * edges you want worn; rounded geometry wears where it curves tightest.
 *
 * Gloss follows the same wear mask: polished edges leave unworn paint
 * at its authored roughness.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material patches every mesh wearing it.
 * The worn area is not one flat colour. Light wear is the paint itself
 * rubbed thin — the surface's OWN tone, lightened and desaturated,
 * which is what keeps the wear belonging to the object it is on — and
 * only the hardest-hit part goes through to `color`, which is itself
 * broken up in value and in warm/cool. A single flat tone painted over
 * every material in a scene is the thing this patch exists to undo.
 *
 * @param {object} [opts] `strength` how far the edge goes toward
 *   `color`, 0..1 (default 0.35); `color` THREE.Color or hex, the bare
 *   substrate under the finish (default a warm bone); `width` the
 *   feature RADIUS in metres
 *   where wear BEGINS, reaching full at a third of it (default 0.35 —
 *   a 12 cm rim wears through, a 2 m barrel is untouched); `seed`
 *   moves the patchy break-up (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`.
 */
export function patchEdgeWear(material, opts = {}) {
  const strength = opts.strength === undefined ? 0.35 : opts.strength;
  const width = opts.width === undefined ? 0.35 : opts.width;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'wear:edge',
    uniforms: {
      uEdgeAmt: { value: unit(strength) },
      uEdgeColor: { value: toColor(opts.color, 0xd2cabb) },
      uEdgeWidth: { value: Math.max(1e-3, width) },
      uEdgeSeed: { value: seedOffset(seed + 0.5) },
    },
    fragmentHead: [
      'uniform float uEdgeAmt;',
      'uniform vec3 uEdgeColor;',
      'uniform float uEdgeWidth;',
      'uniform vec3 uEdgeSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec3 ewN = normalize(vAstraWorldN);',
      // Curvature in 1/m: how far the normal turns per metre the
      // surface travels under one pixel. Positive is convex, and only
      // the convex half wears.
      '  float ewC = astraWearCurv(ewN, vAstraWorld);',
      '  float ewR = 1.0 / uEdgeWidth;',
      '  float ewK = smoothstep(ewR, ewR * 3.0, ewC);',
      // Rubbing is patchy and FINE, at TWO scales. The ramp above is
      // saturated everywhere on anything much tighter than `width` — a
      // 6 cm rail post is 1.0 over every pixel — so a single blotch
      // field is the only structure left there, and a 14 cm blotch on a
      // 12 cm object is a barber pole, not wear. The second octave is
      // the chipping inside each patch; both are tied to the width
      // asked for, so the pattern scales with the feature and a
      // re-render at another distance is the same surface.
      '  vec3 ewAx = astraWearAxes(ewN);',
      '  vec3 ewQ = vAstraWorld * (ewR * 3.4) + uEdgeSeed;',
      '  float ewP = astraWearNoise(ewQ, ewAx);',
      '  float ewF = astraWearNoise(ewQ * 3.3 + 11.3, ewAx);',
      '  float ewG = 1.0 - smoothstep(0.35, 1.00,',
      '                               length(fwidth(ewQ)) * 3.3);',
      '  ewK *= smoothstep(0.36, 0.72, ewP + (ewF - 0.5) * ewG);',
      // Thinned finish first, bare substrate only where it is worn
      // through: the light half keeps the object's OWN hue (lightened
      // and desaturated), and the substrate carries its own value and
      // warm/cool spread, so no two worn patches are the same colour.
      '  float ewL = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));',
      '  vec3 ewThin = mix(diffuseColor.rgb, vec3(ewL), 0.45) * 1.16;',
      '  float ewT = (ewF - 0.5) * ewG * 0.24;',
      '  vec3 ewBare = uEdgeColor * (0.86 + 0.24 * ewP)',
      '              * vec3(1.0 + ewT, 1.0 + ewT * 0.15, 1.0 - ewT);',
      '  vec3 ewTone = mix(ewThin, ewBare, smoothstep(0.35, 0.90, ewK));',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, ewTone,',
      '                         clamp(ewK * uEdgeAmt, 0.0, 1.0));',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, max(0.06, roughnessFactor * 0.58), clamp(ewK * uEdgeAmt, 0.0, 1.0));',
  });
}
