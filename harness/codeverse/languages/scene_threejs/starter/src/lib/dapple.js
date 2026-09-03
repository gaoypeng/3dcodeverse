/**
 * The moving light patches a canopy throws on everything under it.
 *
 * After aerial perspective this is the strongest single cue that a
 * frame is OUTDOORS and under something living: a forest floor, a
 * verandah under a vine, a street under plane trees. Without it the
 * ground under a tree is a flat tone with a shadow blob on it.
 *
 * It goes on the material of the surface BEING LIT, not on the canopy,
 * for the same reason `patchCaustics` does — the pattern belongs to the
 * leaves overhead, which the floor knows nothing about. So the caller
 * states where the canopy is and how dense it is.
 *
 * The gaps SLIDE ALONG THE SUN, not down the world axes: a gap in the
 * canopy projects along the sun direction, so as the pattern drifts in
 * the wind the patch on the ground travels the way the shadow of that
 * gap would. Projecting straight down instead is the tell that turns
 * dapple into a moving texture.
 */

import * as THREE from 'three';
import { patchStandard, composeRoughness } from './shader.js';

// The day rig's sun (azimuth 35, elevation 48), matching water.js and
// river.js, so a surface that was not told where the sun is agrees.
const _AZ = 35 * Math.PI / 180;
const _EL = 48 * Math.PI / 180;
const DAY_SUN = new THREE.Vector3(
    Math.cos(_EL) * Math.cos(_AZ), Math.sin(_EL),
    Math.cos(_EL) * Math.sin(_AZ)).normalize();

// Half the sun's angular diameter, in radians (0.53 deg across). This is
// the ONE number that sets how soft a sunfleck's edge is: the penumbra a
// canopy `h` metres up throws is h * this, ~6 cm under a 7 m crown and
// ~2 cm under a low vine. Measured against it, the edge of a 0.7 m fleck
// is a tenth of the fleck wide — nearly hard. A smoothstep spread over
// the noise's whole swing instead (what this file did until 2026-09-01)
// blurs every fleck into the "cloudy" look the notes below warn about.
const SUN_HALF_ANGLE = 0.00465;

// How much the fbm field's value moves across one unit of its own domain
// — measured over astraFbm2 at 3 octaves. Converting a distance (the
// penumbra, a pixel footprint) into the value-space width a smoothstep
// wants is a multiply by this and nothing more.
const FIELD_SLOPE = 0.26;

/** Warm sun through a gap; the low-sun end is what a fleck goes at dusk. */
const FLECK_HIGH = new THREE.Color(0xffeec0);
const FLECK_LOW = new THREE.Color(0xffc287);

/**
 * Dappled canopy light on the surface under it.
 *
 * The pattern is the union of two drifting fields at different scales,
 * thresholded: leaf shadow is not a smooth wobble but a scatter of
 * bright holes with hard-ish edges, and one octave of noise gives the
 * cloudy blur that reads as fog on the floor instead. It lifts the
 * light rather than the albedo — a sunfleck on a shaded floor is often
 * the only direct light there is, and lifting albedo alone would cap it
 * at the ambient the floor already receives, exactly the choice
 * `windows.js` and `caustics.js` document.
 *
 * Three things make a fleck read as SUNLIGHT rather than as a pale
 * texture, and all three are geometry the caller already stated:
 *  - its edge is a penumbra (`SUN_HALF_ANGLE * height`), not a fade
 *    across the noise's whole swing — and it widens again to whatever
 *    the pixel footprint is, so a floor running to the horizon greys
 *    out instead of crawling;
 *  - it is STRETCHED along the sun's ground track by 1/sin(elevation),
 *    the same way the round gap's shadow is: at a 38 deg sun a fleck is
 *    1.6x longer than it is wide, and it is that elongation, not the
 *    brightness, that says where the sun is;
 *  - its rim is greener than its middle. Light that clipped a leaf on
 *    the way through carries the leaf's transmission, so a fleck runs
 *    warm-white in the core to yellow-green at the edge — a single flat
 *    colour is the tell of a decal.
 *
 * @param {THREE.Material} material Material of the LIT surface, patched
 *   in place. Needs a lit material with an `emissive` (a Standard or
 *   Physical); a Basic has none and a raw ShaderMaterial ignores this.
 * @param {object} [opts] `sunDir` (THREE.Vector3) direction TOWARD the
 *   sun, the same vector the sun light sits on (default the day rig's);
 *   `height` metres from this surface up to the canopy (default 6) —
 *   what sets how far the pattern slides as it drifts AND how soft the
 *   flecks are, so a low vine and a high crown neither move nor blur
 *   alike; `scale` metres across one leaf gap (default 0.7); `density`
 *   0..1 how much canopy there is (default 0.5) — 0 is open sky and
 *   lights everything, 1 is closed and lets almost nothing through;
 *   `strength` how bright a fleck goes (default 0.55); `speed` metres
 *   per second the canopy drifts (default 0.35); `color` (THREE.Color
 *   or hex) the light's own colour — the default is read off `sunDir`,
 *   warm-white overhead and orange near the horizon, so a dusk scene
 *   does not get noon flecks; `leaf` (THREE.Color or hex) what light
 *   through the leaf itself carries, the fleck's rim colour (default a
 *   yellow-green); `seed`.
 * @returns {THREE.Material} The same material.
 */
export function patchDappledLight(material, opts = {}) {
  const sun = (opts.sunDir ? opts.sunDir.clone() : DAY_SUN.clone())
      .normalize();
  const height = opts.height === undefined ? 6 : opts.height;
  const scale = Math.max(0.05, opts.scale === undefined ? 0.7 : opts.scale);
  const density = Math.min(1, Math.max(0,
      opts.density === undefined ? 0.5 : opts.density));
  const strength = opts.strength === undefined ? 0.55 : opts.strength;
  const speed = opts.speed === undefined ? 0.35 : opts.speed;
  // A fleck is a picture of THE SUN, so its colour is the sun's: the
  // caller who set the rig low has already said the light is orange.
  const warm = Math.min(1, Math.max(0, (sun.y - 0.05) / 0.45));
  const color = opts.color === undefined
      ? FLECK_LOW.clone().lerp(FLECK_HIGH, warm * warm * (3 - 2 * warm))
      : new THREE.Color(opts.color);
  const leaf = new THREE.Color(opts.leaf === undefined ? 0x9ec06a : opts.leaf);
  const seed = opts.seed === undefined ? 1 : opts.seed;

  if (!material.emissive) {
    console.warn('patchDappledLight: needs a lit material (Standard or '
        + 'Physical) — a sunfleck is light, not paint.');
  }
  // A canopy makes the floor under it damp and matte more often than
  // not; a whole factor would fight a wet patch, so this is gentle.
  composeRoughness(material, 'dapple', 1 + 0.06 * density);

  // A fleck is the light that MISSED the canopy, so the albedo
  // `patchCanopyShade` took away (its stand-in for the shadow three
  // will not cast) must not be taken away from the fleck as well —
  // that is what made a fleck in deep shade dimmer than one at the
  // edge of the crown, which is backwards. The shade runs first, so
  // its average cut is known here; give it back, capped, and only
  // when the pair is really in use.
  const can = material.userData.astraCanopy;
  const comp = can
      ? Math.min(1.7, 1 / Math.max(0.4,
          1 - can.depth * can.density * (0.72 + 0.28 * can.density)))
      : 1;

  return patchStandard(material, {
    name: 'dapple',
    uniforms: {
      uDapSun: { value: sun },
      uDapH: { value: height },
      uDapScale: { value: scale },
      uDapDens: { value: density },
      uDapAmt: { value: strength * comp },
      uDapSpeed: { value: speed },
      uDapColor: { value: color },
      uDapLeaf: { value: leaf },
      uDapSeed: { value: seed },
    },
    vertexHead: 'varying vec3 vAstraDapW;\nvarying vec3 vAstraDapN;',
    vertexBody: [
      '#ifdef USE_INSTANCING',
      '  vAstraDapW = (modelMatrix * instanceMatrix'
          + ' * vec4(transformed, 1.0)).xyz;',
      '  vAstraDapN = normalize(mat3(modelMatrix)'
          + ' * mat3(instanceMatrix) * normal);',
      '#else',
      '  vAstraDapW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
      '  vAstraDapN = normalize(mat3(modelMatrix) * normal);',
      '#endif',
    ].join('\n'),
    fragmentHead: [
      'varying vec3 vAstraDapW;',
      'varying vec3 vAstraDapN;',
      'uniform float uTime;',
      'uniform vec3 uDapSun;',
      'uniform float uDapH;',
      'uniform float uDapScale;',
      'uniform float uDapDens;',
      'uniform float uDapAmt;',
      'uniform float uDapSpeed;',
      'uniform vec3 uDapColor;',
      'uniform vec3 uDapLeaf;',
      'uniform float uDapSeed;',
      // Gaps, not clouds: fbm thresholded well above its mean leaves
      // scattered holes with edges. Two scales union because a canopy
      // has both leaf gaps and branch gaps — the leaf field is the
      // common one and the branch field the rare wide hole, so its
      // threshold sits ABOVE the other's; equal thresholds (what this
      // did before) let the coarse field cover the floor and every
      // fleck came out a metre-wide puddle.
      //
      // `w` is the edge width in VALUE space — the penumbra and the
      // pixel, converted by the field's own slope; the branch field is
      // 2.7x coarser, so the same distance is a smaller fraction of
      // its swing.
      //
      // Returned as (gap, core): the core is the same gap eroded by
      // one threshold step, i.e. the part of it the whole sun reaches.
      // Taking the core off the gap VALUE instead would make the rim
      // a couple of pixels wide once the edge is properly hard, and
      // the leaf-green it carries would never be seen.
      'vec2 astraDapGaps(vec2 p, float d, float w) {',
      '  float a = astraFbm2(p, 3);',
      '  float b = astraFbm2(p * 0.37 + 11.3, 2);',
      // A canopy is not evenly thin: gaps CROWD where the crown is
      // sparse and give out under a thick one. Modulating the
      // threshold at crown scale is what turns an even scatter —
      // which reads as camouflage — into flecks that belong to a
      // tree. This field drifts with the rest, so a thin patch of
      // canopy travels over the floor as one.
      '  float c = astraFbm2(p * 0.13 + 5.1, 2);',
      '  float t = mix(0.28, 0.76, d) + (c - 0.375) * 0.45;',
      '  float wb = 0.05 + w * 0.5;',
      '  float ga = smoothstep(t - w, t + w, a);',
      '  float gb = smoothstep(t + 0.10 - wb, t + 0.10 + wb, b);',
      '  float ca = smoothstep(t + 0.035 - w, t + 0.035 + w, a);',
      '  float cb = smoothstep(t + 0.145 - wb, t + 0.145 + wb, b);',
      '  return vec2(max(ga, gb * 0.9), max(ca, cb * 0.9));',
      '}',
    ].join('\n'),
    fragmentBody: [
      // Where this fragment's sunbeam CROSSED the canopy. Sliding the
      // sample point along the sun (not straight up) is what makes a
      // fleck travel the way the gap's shadow would.
      '  vec3 dpS = normalize(uDapSun);',
      '  float dpUp = max(dpS.y, 0.15);',
      '  vec2 dpAt = vAstraDapW.xz + dpS.xz * (uDapH / dpUp);',
      '  vec2 dpP = dpAt / uDapScale + uDapSeed * 17.0;',
      // The canopy drifts across the wind; the floor sees that drift
      // magnified by how far overhead it is.
      '  dpP += vec2(1.0, 0.35) * (uTime * uDapSpeed / uDapScale);',
      // The gap's shadow is an ellipse: a slanted beam lands long. The
      // sample is COMPRESSED along the sun's ground track, which is
      // what stretches the pattern out along it in the world.
      '  vec2 dpAx = dpS.xz;',
      '  float dpAxL = length(dpAx);',
      '  if (dpAxL > 1e-4) {',
      '    dpAx /= dpAxL;',
      '    float dpEl = clamp(1.0 / dpUp, 1.0, 3.0);',
      '    dpP += dpAx * (dot(dpP, dpAx) * (1.0 / dpEl - 1.0));',
      '  }',
      // Edge = the sun's penumbra at this height, never finer than the
      // pixel: the second term is what keeps a receding floor from
      // boiling into noise.
      '  float dpPen = ' + SUN_HALF_ANGLE.toFixed(5) + ' * uDapH / uDapScale;',
      '  float dpAA = fwidth(dpP.x) + fwidth(dpP.y);',
      '  float dpW = clamp((dpPen + dpAA) * ' + FIELD_SLOPE.toFixed(2) + ','
          + ' 0.012, 0.40);',
      '  vec2 dpGC = astraDapGaps(dpP, uDapDens, dpW);',
      '  float dpG = dpGC.x;',
      // Only what faces the sun catches a fleck, and a fleck on a
      // surface already in full sun adds nothing anyone can see. The
      // knee keeps a grazing face from wearing a hard lambert line.
      '  float dpF = max(dot(normalize(vAstraDapN), dpS), 0.0);',
      '  dpF *= smoothstep(0.0, 0.30, dpF);',
      // Rim green, core sun, and no two flecks quite the same colour.
      '  float dpCore = dpGC.y;',
      '  vec3 dpTint = mix(uDapLeaf, uDapColor, dpCore);',
      '  dpTint = astraHueBreak(dpTint, dpP, 0.23, 0.34);',
      // The core of a fleck is the whole sun; its rim is a sliver of
      // one. That ratio is what gives a bloom pass something to find.
      '  float dpBeam = dpG * (1.0 + 2.2 * dpCore);',
      '  totalEmissiveRadiance += dpTint * (uDapAmt * dpBeam * dpF'
          + ' * diffuseColor.rgb);',
    ].join('\n'),
  });
}

/**
 * The shade a canopy casts, as a floor-level tint rather than a shadow.
 *
 * Three's shadow maps do not reach an instanced crown of billboards,
 * and a forest floor lit exactly like open ground reads as a lawn with
 * trees standing on it. This darkens and cools the surface by how much
 * canopy is overhead, so `patchDappledLight`'s flecks have something
 * to be flecks ON. Apply it before the dapple; both compose.
 *
 * `density` is COVERAGE: at 0.5 about half the floor is under crown.
 * Until 2026-09-01 the depth of the shade was multiplied by the density
 * as well as thresholded by it, so the default asked for half a canopy
 * and got a 9% darkening — measured on our renderer, a 28 m floor came
 * back with a 0.07 luminance range, i.e. a flat card. The shade is the
 * expensive half of this file: flecks are only bright relative to it.
 *
 * @param {THREE.Material} material Material of the shaded surface.
 * @param {object} [opts] `density` 0..1 (default 0.5); `scale` metres
 *   across one crown (default 6) — this is the CROWN scale, an order
 *   above the leaf-gap scale, so the two never beat together; `cool`
 *   (THREE.Color or hex) what the shade tints toward, default the green
 *   a canopy bounces down; `depth` 0..1 how dark the deepest shade goes
 *   (default 0.80 — never to black: what light a forest floor has is
 *   sky and bounce, and crushing it is the other way to lose the
 *   effect); `seed`.
 * @returns {THREE.Material} The same material.
 */
export function patchCanopyShade(material, opts = {}) {
  const density = Math.min(1, Math.max(0,
      opts.density === undefined ? 0.5 : opts.density));
  const scale = Math.max(0.5, opts.scale === undefined ? 6 : opts.scale);
  const cool = new THREE.Color(
      opts.cool === undefined ? 0x5d6b48 : opts.cool);
  const depth = Math.min(1, Math.max(0,
      opts.depth === undefined ? 0.80 : opts.depth));
  const seed = opts.seed === undefined ? 1 : opts.seed;
  // What `patchDappledLight` gives back to its flecks; see there.
  material.userData.astraCanopy = { density, depth };
  return patchStandard(material, {
    name: 'canopyShade',
    uniforms: {
      uCanDens: { value: density },
      uCanScale: { value: scale },
      uCanCool: { value: cool },
      uCanDepth: { value: depth },
      uCanSeed: { value: seed },
    },
    vertexHead: 'varying vec3 vAstraCanW;',
    vertexBody: [
      '#ifdef USE_INSTANCING',
      '  vAstraCanW = (modelMatrix * instanceMatrix'
          + ' * vec4(transformed, 1.0)).xyz;',
      '#else',
      '  vAstraCanW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
      '#endif',
    ].join('\n'),
    fragmentHead: [
      'varying vec3 vAstraCanW;',
      'uniform float uCanDens;',
      'uniform float uCanScale;',
      'uniform vec3 uCanCool;',
      'uniform float uCanDepth;',
      'uniform float uCanSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec2 cnP = vAstraCanW.xz / uCanScale + uCanSeed * 5.0;',
      // Crowns, and the grove they stand in: one field alone lays every
      // tree down at the same size and the floor reads as a texture.
      '  float cnA = astraFbm2(cnP, 3);',
      '  float cnB = astraFbm2(cnP * 0.41 + 3.7, 2);',
      '  float cnF = cnA * 0.72 + cnB * 0.28;',
      // The threshold, not the amplitude, is what `density` moves: the
      // field crosses it over about `density` of the ground.
      '  float cnT = mix(0.72, 0.10, uCanDens);',
      // A 28 m floor is 4 crowns across and every gradient on it is
      // gentle enough to band on an 8-bit target; one bit of hash is
      // cheaper than any dither texture.
      '  float cnD = (astraHash21(gl_FragCoord.xy) - 0.5) * 0.012;',
      '  float cnK = smoothstep(cnT, cnT + 0.30, cnF + cnD);',
      // Deep shade is not just dark, it is GREEN — that light came
      // through leaves — and no two patches of it are the same green.
      '  vec3 cnTint = astraHueBreak(uCanCool, cnP, 1.7, 0.34);',
      // Under the thickest crown the last light left is SKY, not the
      // green bounced off the leaf above — so the deep end of the
      // shade leans blue while its edge stays leaf-green. One flat
      // tint over the whole range is what leaves shade reading as
      // grey paint.
      '  cnTint *= mix(vec3(1.0), vec3(0.84, 0.95, 1.14),',
      '                smoothstep(0.5, 1.0, cnK));',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, cnTint, cnK * 0.45);',
      '  diffuseColor.rgb *= 1.0 - uCanDepth * cnK'
          + ' * mix(0.72, 1.0, uCanDens);',
    ].join('\n'),
  });
}
