/**
 * Three patches that make a plant read as ALIVE rather than as green
 * geometry: leaves that transmit the sun from behind, a sway that bends
 * at the tip and holds at the base, and the litter ring where a trunk
 * meets the ground.
 *
 * All three go through `patchStandard`, so the material keeps its
 * lighting, shadows, fog and depth chunks, and they compose: leaves
 * that both glow and sway are two calls on one leaf material.
 */

import * as THREE from 'three';
import { glslLocalDir, patchStandard, readWind } from './shader.js';

// three applies instanceMatrix in <project_vertex>, AFTER the body
// patched in here, so anything that needs world space at this point has
// to apply it by hand. That is the whole reason these helpers exist.
const SHARED_VERTEX_HEAD = [
  'varying vec3 vAstraFol;',
  'vec3 astraFolWorld(vec3 p) {',
  '#ifdef USE_INSTANCING',
  '  return (modelMatrix * instanceMatrix * vec4(p, 1.0)).xyz;',
  '#else',
  '  return (modelMatrix * vec4(p, 1.0)).xyz;',
  '#endif',
  '}',
  glslLocalDir('astraFolLocalDir', true),
].join('\n');

// Goes on once however many patches a material carries: patchStandard
// chains them and dedupes varyings, but a second copy of a FUNCTION is
// a GLSL redefinition error that takes the whole plant with it.
const SHARED_PATCH = {
  name: 'foliageBase',
  vertexHead: SHARED_VERTEX_HEAD,
  vertexBody: '  vAstraFol = astraFolWorld(transformed);',
  fragmentHead: 'varying vec3 vAstraFol;',
};

/**
 * Apply one patch once, over the shared world-space preamble.
 *
 * `patchStandard` chains patches and injects each body at its hook, so
 * every patch here has to stand on its own — none may read a local a
 * sibling declared. A repeat call retunes the uniforms rather than
 * injecting the same declarations twice.
 */
function addPatch(material, part) {
  const applied = material.userData.astraFoliage || {};
  material.userData.astraFoliage = applied;
  if (applied[part.name]) {
    for (const key of Object.keys(part.uniforms)) {
      material.userData.uniforms[key].value = part.uniforms[key].value;
    }
    return material;
  }
  if (!applied.base) {
    applied.base = true;
    patchStandard(material, SHARED_PATCH);
  }
  applied[part.name] = true;
  return patchStandard(material, part);
}

/**
 * Backlit leaves glow — the strongest single cue that a tree is alive.
 *
 * Transmission uses the first directional light's radiance and shadow map.
 * It vanishes when that light is off or occluded. A broad back-surface term
 * and a narrower forward-scattering lobe reveal thin leaves without raising
 * their albedo. Pigment variation remains a separate material treatment.
 *
 * @param {THREE.Material} material Leaf material, patched in place.
 * @param {object} [opts] `sunDir` (THREE.Vector3) direction TOWARD the
 *   sun; optional direction override, otherwise the first scene directional
 *   light supplies the direction. At night use the moon's direction; `tint`
 *   (THREE.Color) the transmitted colour, the warm yellow-green a leaf
 *   turns when lit through; `light` (THREE.Color) the key light's own
 *   colour, multiplied into that tint so a golden or moonlit rig
 *   transmits its own light rather than a hardcoded noon (default
 *   white); `strength` (default 0.55) transmitted-light amount;
 *   `power` (default 3) how tight the lobe is around the sun — lower
 *   spreads the glow wider off axis; `variance` (default 0.6) the
 *   pigment swing, 0 to leave the albedo alone; `varyScale` (default 1)
 *   scales the break-up in world metres — raise it for grass and small
 *   leaves, lower it for a canopy read from far away.
 * @returns {THREE.Material} The same material.
 */
export function patchLeafSSS(material, opts = {}) {
  const sun = (opts.sunDir || new THREE.Vector3(0.45, 0.75, 0.35))
      .clone().normalize();
  const tint = new THREE.Color(
      opts.tint === undefined ? 0xccd966 : opts.tint);
  if (opts.light !== undefined) tint.multiply(new THREE.Color(opts.light));
  const strength = opts.strength === undefined ? 0.55 : opts.strength;
  const power = opts.power === undefined ? 3 : opts.power;
  const variance = opts.variance === undefined ? 0.6 : opts.variance;
  const varyScale = opts.varyScale === undefined ? 1 : opts.varyScale;
  return addPatch(material, {
    name: 'leaf',
    uniforms: {
      uLeafSun: { value: sun },
      uLeafExplicit: { value: opts.sunDir ? 1 : 0 },
      uLeafTint: { value: tint },
      uLeafAmt: { value: strength },
      uLeafPow: { value: power },
      uLeafVar: { value: variance },
      uLeafVarS: { value: varyScale },
    },
    fragmentHead: [
      'uniform vec3 uLeafSun;',
      'uniform float uLeafExplicit;',
      'uniform vec3 uLeafTint;',
      'uniform float uLeafAmt;',
      'uniform float uLeafPow;',
      'uniform float uLeafVar;',
      'uniform float uLeafVarS;',
    ].join('\n'),
    fragmentBody: [
      // PIGMENT first, so everything below rides varied leaves. y is
      // folded into the horizontal sample so a crown breaks up through
      // its depth too — an xz-only field paints the same tone down a
      // whole column of leaves.
      '  vec2 lP = vAstraFol.xz + vAstraFol.y * 0.8;',
      // Two scales, both COARSE. astraHueBreak's stated foliage swing is
      // a ground mat's, and a crown needs the hue to travel further, so
      // the swing is 3x it at clump scale (~2 m: this bough against
      // that one) and half that a metre down (~0.5 m: within a clump).
      // A third, finer octave was tried and cut — smooth noise under a
      // metre stops reading as leaves and starts reading as camouflage
      // sprayed over the mass.
      '  diffuseColor.rgb = astraHueBreak(diffuseColor.rgb, lP,',
      '      0.55 * uLeafVarS, uLeafVar * 3.0);',
      '  diffuseColor.rgb = astraHueBreak(diffuseColor.rgb, lP,',
      '      1.9 * uLeafVarS, uLeafVar * 1.4);',
      // Value too, or the crown is one brightness in many hues — and on
      // the SAME field as the clump break (astraHueBreak's own 2-octave
      // sample at that scale), because the two are one story: new growth
      // that caught the sun is warm AND light, the mass behind it is
      // cool AND dark. Run at an unrelated scale instead — which is
      // where this started — the warm swing lands as often on a dark
      // clump as a light one and the crown goes olive. Stretched to the
      // field's own range and CLAMPED: unbounded, a rare tail would take
      // a leaf past the albedo range at either end.
      '  float lVar = clamp((astraFbm2(lP * 0.55 * uLeafVarS, 2)',
      '      - 0.375) * 2.6, -1.0, 1.0);',
      '  diffuseColor.rgb *= 1.0 + uLeafVar * 0.5 * lVar;',
      // Thin-leaf transmission is incident light, not an albedo multiplier.
      // The first directional light supplies irradiance and shadow visibility;
      // a supplied sunDir overrides only the lobe direction for older callers.
      '  #if NUM_DIR_LIGHTS > 0',
      '    vec3 lV = normalize(vViewPosition);',
      '    vec3 lSun = normalize(mix(directionalLights[0].direction,',
      '      mat3(viewMatrix) * uLeafSun, uLeafExplicit));',
      '    #ifdef FLAT_SHADED',
      '      vec3 lN = normalize(cross(dFdx(-vViewPosition), dFdy(-vViewPosition)));',
      '    #else',
      '      vec3 lN = normalize(vNormal);',
      '    #endif',
      '    lN *= dot(lN, lV) < 0.0 ? -1.0 : 1.0;',
      '    float lBack = max(-dot(lN, lSun), 0.0);',
      '    float lForward = pow(max(dot(lV, -lSun), 0.0), uLeafPow);',
      '    float lT = uLeafAmt * (0.65 * lBack + 0.35 * lForward);',
      '    float lVisibility = 1.0;',
      '    #if defined(USE_SHADOWMAP) && NUM_DIR_LIGHT_SHADOWS > 0',
      '      lVisibility = getShadow(directionalShadowMap[0], directionalLightShadows[0].shadowMapSize,',
      '        directionalLightShadows[0].shadowIntensity, directionalLightShadows[0].shadowBias,',
      '        directionalLightShadows[0].shadowRadius, vDirectionalShadowCoord[0]);',
      '    #endif',
      '    totalEmissiveRadiance += uLeafTint * lT * directionalLights[0].color',
      '      * lVisibility * sqrt(max(diffuseColor.rgb, vec3(0.0))) * 0.5;',
      '  #endif',
    ].join('\n'),
  });
}

/**
 * Wind sway: the plant bends at the tip and stays planted at the base.
 *
 * The bend is normalised by the plant's own height and squared, so the
 * base cannot move and the tip carries the whole travel. The phase
 * comes from each plant's WORLD origin, staggered by several turns:
 * a per-vertex phase would tear one plant apart and a shared phase
 * makes a whole row nod in step. Deterministic — the world position is
 * the seed, there is no PRNG. Drive it with `tickShaders(scene, t)`;
 * an un-advanced uTime is a frozen plant, not a broken patch.
 *
 * Works on an InstancedMesh as well as a Mesh (instanceMatrix is
 * applied by hand here), but a swaying plant's SHADOW stays still:
 * three draws shadows with a depth material this patch never sees.
 *
 * @param {THREE.Material} material Leaf or bark material, patched in
 *   place.
 * @param {object} [opts] `strength` metres of travel at full bend
 *   (default 0.25); `speed` (default 0.9) radians per second; `height`
 *   (default 4) the plant's height in its OWN units, which the bend is
 *   normalised by — the asset's base must sit at y = 0 for that to
 *   mean anything; `dir` (THREE.Vector2) wind direction over world XZ
 *   (default a light south-easterly), normalised for you.
 * @returns {THREE.Material} The same material.
 */
export function patchWind(material, opts = {}) {
  const dir = readWind(opts.dir, [1, 0.35], 'patchWind dir').dir;
  const strength = opts.strength === undefined ? 0.25 : opts.strength;
  const speed = opts.speed === undefined ? 0.9 : opts.speed;
  const height = opts.height === undefined ? 4 : opts.height;
  return addPatch(material, {
    name: 'wind',
    uniforms: {
      uWindAmp: { value: strength },
      uWindSpeed: { value: speed },
      uWindH: { value: height },
      uWindDir: { value: dir },
    },
    vertexHead: [
      // patchStandard declares uTime wherever it is read — its withTime
      // pass runs before the body is injected — so a body that reads
      // uTime without this line fails to compile.
      'uniform float uTime;',
      'uniform float uWindAmp;',
      'uniform float uWindSpeed;',
      'uniform float uWindH;',
      'uniform vec2 uWindDir;',
    ].join('\n'),
    vertexBody: [
      '  float wB = clamp(transformed.y / max(uWindH, 1e-3), 0.0, 1.0);',
      '  vec3 wO = astraFolWorld(vec3(0.0));',
      '  float wP = uTime * uWindSpeed + astraStagger(wO.x + wO.z * 0.37);',
      '  float wS = (sin(wP) + 0.35 * sin(wP * 2.3 + 1.7)) / 1.35;',
      // The wind blows ONE way over the whole scene, so its world
      // direction is carried into the plant's own frame — a scatter
      // gives every instance a different yaw.
      '  vec3 wD = astraFolLocalDir(vec3(uWindDir.x, 0.0, uWindDir.y));',
      '  float wDerivative = transformed.y > 0.0 && transformed.y < uWindH',
      '      ? 2.0 * uWindAmp * wB * wS / max(uWindH, 1e-3) : 0.0;',
      '  transformed += wD * (uWindAmp * wB * wB * wS);',
      '  #ifndef FLAT_SHADED',
      '    mat3 wJacobian = mat3(vec3(1.0, 0.0, 0.0),',
      '      vec3(0.0, 1.0, 0.0) + wD * wDerivative, vec3(0.0, 0.0, 1.0));',
      '    vec3 wN = astraNormalTransform(wJacobian, objectNormal);',
      '    #ifdef USE_INSTANCING',
      '      wN = astraNormalTransform(mat3(instanceMatrix), wN);',
      '    #endif',
      '    transformedNormal = normalMatrix * wN;',
      '    vNormal = normalize(transformedNormal);',
      '  #endif',
    ].join('\n'),
  });
}

/**
 * Seat a trunk in the ground: contact shade plus a ring of litter.
 *
 * Nothing in a photograph meets anything else with a hard edge, and a
 * trunk whose colour stops dead at the ground reads as a cylinder
 * standing ON a plane. This darkens the last `band` metres above the
 * plant's own base and tints them toward fallen leaves, with the ring's
 * height moved by horizontal noise so the line runs ragged AROUND the
 * trunk instead of sitting level like a decal.
 *
 * @param {THREE.Material} material Trunk material, patched in place.
 *   Height is measured from the object's ORIGIN, so the asset's base
 *   must sit at y = 0.
 * @param {object} [opts] `band` metres above the contact plane that the
 *   shading fades over (default 0.5); `darken` (default 0.45) how much
 *   darker the very base goes; `litter` (THREE.Color) the fallen-leaf
 *   colour the base tints toward.
 * @returns {THREE.Material} The same material.
 */
export function patchRootContact(material, opts = {}) {
  const band = opts.band === undefined ? 0.5 : opts.band;
  const darken = opts.darken === undefined ? 0.45 : opts.darken;
  const litter = new THREE.Color(
      opts.litter === undefined ? 0x4a3a26 : opts.litter);
  return addPatch(material, {
    name: 'root',
    uniforms: {
      uRootBand: { value: band },
      uRootDark: { value: darken },
      uRootLitter: { value: litter },
    },
    vertexHead: 'varying float vAstraRootH;',
    vertexBody: '  vAstraRootH = astraFolWorld(transformed).y'
        + ' - astraFolWorld(vec3(0.0)).y;',
    fragmentHead: [
      'varying float vAstraRootH;',
      'uniform float uRootBand;',
      'uniform float uRootDark;',
      'uniform vec3 uRootLitter;',
    ].join('\n'),
    fragmentBody: [
      // The band's own structure runs across it: horizontal noise moves
      // the HEIGHT the litter reaches, so its edge is ragged around the
      // trunk rather than a level ring. The field is sampled at 7 per
      // metre, not 3: a trunk is half a metre wide, and one cycle
      // across it is a tilted line, not a ragged one.
      '  float rH = vAstraRootH - uRootBand * 0.55 *',
      '      (astraFbm2(vAstraFol.xz * 7.0, 3) - 0.4);',
      '  float rK = astraContact(rH, uRootBand);',
      // Fallen leaves are not one brown: warm dry litter next to damp
      // dark, at the scale of a handful of leaves.
      '  vec3 rL = astraHueBreak(uRootLitter, vAstraFol.xz, 9.0, 0.7);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, rL, rK * rK * 0.7);',
      '  diffuseColor.rgb *= 1.0 - uRootDark * rK;',
    ].join('\n'),
  });
}
