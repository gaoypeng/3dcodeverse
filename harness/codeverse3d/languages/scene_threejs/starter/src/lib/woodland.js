/**
 * The two halves of a forest: the trunk the camera walks up to, and the
 * stand-ins for everything too far away to be worth geometry.
 *
 * `patchBark` shades a trunk. Bark is the one surface in a scene the
 * viewer has touched, so a flat brown cylinder is read instantly as a
 * placeholder — and the cue is not roughness, it is DIRECTION: a trunk's
 * fissures are long and narrow and they run UP. Isotropic noise on a
 * trunk reads as stone. The field here is sampled with its along-trunk
 * axis divided by `aniso`, so every feature is stretched that many times
 * up the trunk before anything else happens to it.
 *
 * `makeImposters` puts a silhouette on a card. A hundred real trees is a
 * budget; a hundred cards is one draw call, and past ~35 m nobody can
 * tell (measured — see the function's own note). Cards carry the cost of
 * a forest, a crowd or a distant town in the only place it is affordable.
 *
 * Both go through `lib/shader.js`, so both keep the depth and fog chunks
 * that decide whether an effect is in the frame at all.
 */

import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';

import { mulberry32 } from './noise.js';
import { windOf } from './grass.js';
import {
  composeRoughness, glslLocalDir, instancedQuad, keepOutOfDepthPasses,
  makeShaderMaterial, patchStandard, readVec3, seedVec3, tickShaders,
  toColor, unit,
} from './shader.js';

const _TAU = Math.PI * 2;

/**
 * The four barks, as UNIFORM values over one shared program.
 *
 * `aniso` is how many times longer a fissure is than it is wide;
 * `crack` is its half-width and its contrast; `node` is the metres
 * between a birch's peel seams or a bamboo's nodes.
 */
// `tint` is an ALBEDO: nothing here goes over 0.80 on a channel, or the
// bark clips before the tone map can shape it. Birch was 0xd8d4c8
// (0.85) and rendered as a white pole on this pipeline — measured 0.746
// mean luminance with a p95 of 0.833, the brightest thing in a daylight
// frame including the sky's own gradient.
const BARK_KINDS = {
  pine: { id: 0, tint: 0x8f5a33, scale: 0.075, aniso: 5.5,
          crack: [0.25, 0.85], plate: [0.55, 1.00], node: 0, rough: 1.22 },
  birch: { id: 1, tint: 0xc2beb1, scale: 0.110, aniso: 6.0,
           crack: [0.13, 0.55], plate: [0, 0], node: 0.55, rough: 1.08 },
  oak: { id: 2, tint: 0x6f5940, scale: 0.085, aniso: 10.5,
         crack: [0.24, 1.00], plate: [0, 0], node: 0, rough: 1.30 },
  bamboo: { id: 3, tint: 0x9aa858, scale: 0.160, aniso: 12.0,
            crack: [0.07, 0.35], plate: [0, 0], node: 0.34, rough: 0.72 },
};

const seedOffset = (seed) => seedVec3(seed, 0.29, 4.13, 9.67, 48);

const BARK_VARYINGS = [
  'varying vec3 vAstraBarkP;',
  'varying vec3 vAstraBarkN;',
  'varying vec3 vAstraBarkS;',
].join('\n');

// The grain lives in the TRUNK's own frame, so the sun has to come down
// into it; three applies instanceMatrix after this hook, hence the
// branch. Projection, not inverse: compose() leaves the columns
// orthogonal.
const BARK_VERTEX_HEAD = [
  BARK_VARYINGS,
  'uniform vec3 uBarkSun;',
  glslLocalDir('astraBarkLocalDir', true),
].join('\n');

const BARK_FRAGMENT_HEAD = [
  BARK_VARYINGS,
  'uniform float uBarkKind;',
  'uniform float uBarkScale;',
  'uniform float uBarkAniso;',
  'uniform float uBarkDepth;',
  'uniform float uBarkMoss;',
  'uniform vec2 uBarkCrack;',
  'uniform vec2 uBarkPlate;',
  'uniform float uBarkNode;',
  'uniform vec3 uBarkAxis;',
  'uniform vec3 uBarkTint;',
  'uniform vec3 uBarkMossColor;',
  'uniform vec3 uBarkSeed;',
  'vec3 astraBarkPerp(vec3 ax) {',
  '  vec3 g = abs(ax.y) > 0.9 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);',
  '  return normalize(cross(ax, g));',
  '}',
  // Two 2D slices that SHARE the along-trunk coordinate: one divide by
  // uBarkAniso then stretches every feature up the trunk at once, which
  // is the whole difference between bark and gravel.
  'float astraBarkFbm(vec2 q, float h) {',
  '  return 0.5 * (astraFbm2(vec2(q.x, h), 3)',
  '              + astraFbm2(vec2(q.y, h) + 31.7, 3));',
  '}',
  // 0 ON a fissure line, 1 in the middle of a ridge: a fissure is where
  // the field crosses its own median, which is a CURVE, not a blob.
  // The gain sets how fast the ridge saturates, and it is what decides
  // whether the cut reads as a LINE or as a cloud: at 4.8 the sub-1 band
  // was wide enough that the kind's own cut width turned it into
  // camouflage blotches on our render. 6.4 keeps the same curves and
  // makes the dark part of them narrow.
  'float astraBarkRidge(vec2 q, float h) {',
  '  return clamp(abs(astraBarkFbm(q, h) - 0.4375) * 6.4, 0.0, 1.0);',
  '}',
].join('\n');

const BARK_FRAGMENT_BODY = [
  '  vec3 bkAx = normalize(uBarkAxis);',
  '  vec3 bkT1 = astraBarkPerp(bkAx);',
  '  vec3 bkT2 = cross(bkAx, bkT1);',
  '  float bkSc = max(uBarkScale, 1e-3);',
  // ALONG is divided by aniso and ACROSS is not. This one asymmetry is
  // the claim the whole patch makes.
  '  float bkH = dot(vAstraBarkP, bkAx) / (bkSc * max(uBarkAniso, 1.0))',
  '            + uBarkSeed.z;',
  '  vec2 bkQ = vec2(dot(vAstraBarkP, bkT1), dot(vAstraBarkP, bkT2))',
  '           / bkSc + uBarkSeed.xy;',
  '  float bkV = astraBarkRidge(bkQ, bkH);',
  '  float bkCut = 1.0 - smoothstep(0.0, uBarkCrack.x, bkV);',
  // The second, finer set of cracks is scaled 3.1x ACROSS the trunk and
  // only 1.35x along it: scaling both (it was 2.7 and 2.7) kept the
  // aspect and so produced short strokes, which is the speckle that read
  // as camouflage rather than as grain. Narrow and LONG is the whole
  // point of the module.
  '  float bkV2 = astraBarkRidge(bkQ * 3.1 + 9.3, bkH * 1.35);',
  '  float bkFine = 1.0 - smoothstep(0.0, uBarkCrack.x * 0.5, bkV2);',
  '  bkCut = clamp(bkCut + 0.40 * bkFine * (1.0 - bkCut), 0.0, 1.0);',
  // HUE VARIANCE, before the kind's own structure. A trunk is never one
  // colour — sapwood, weathering and the side the rain runs down move it
  // warm and cool over half a metre — and a flat tint is what makes a
  // shaded trunk read as a brown cylinder however good the fissures are.
  // The same field, read very coarsely, so the drift follows the GRAIN
  // instead of spotting the trunk.
  '  float bkDr = astraBarkFbm(bkQ * 0.16, bkH * 0.16 + 5.3) - 0.4375;',
  '  vec3 bkTint = uBarkTint * vec3(1.0 + bkDr * 1.10,',
  '                                 1.0 + bkDr * 0.26, 1.0 - bkDr * 0.85);',
  '  float bkFlat = 0.0;',
  '  if (uBarkKind < 0.5) {',
  // PINE sheds in plates the size of a hand: a second field, coarse and
  // barely stretched, cut at its own median into cells.
  '    float bkPl = astraBarkRidge(bkQ * uBarkPlate.x,',
  '                                bkH * uBarkPlate.y + 4.7);',
  '    float bkGap = 1.0 - smoothstep(0.0, 0.18, bkPl);',
  '    bkCut = clamp(max(bkCut * 0.45, bkGap * 0.9), 0.0, 1.0);',
  '    bkTint = mix(bkTint, bkTint * vec3(1.35, 0.95, 0.70), bkPl);',
  '  } else if (uBarkKind < 1.5) {',
  // BIRCH is the one bark whose marks run ACROSS: lenticel dashes over
  // a papery skin the vertical grain barely scores.
  '    float bkLn = astraBarkRidge(bkQ * 1.1 + 3.1, bkH * 11.0);',
  '    float bkDash = 1.0 - smoothstep(0.0, 0.12, bkLn);',
  '    float bkPeel = 1.0 - smoothstep(0.0, 0.10,',
  '        astraBarkRidge(bkQ * 0.12 + 7.7, bkH * uBarkNode));',
  '    bkCut = clamp(bkCut * 0.22 + bkDash * 0.65 + bkPeel * 0.55,',
  '                  0.0, 1.0);',
  '  } else if (uBarkKind < 2.5) {',
  // OAK: the deepest fissures, and the flats between them are the
  // ridge TOPS, which catch the light rather than only losing it.
  '    bkCut = clamp(bkCut * 1.2, 0.0, 1.0);',
  '    bkFlat = smoothstep(0.55, 0.95, bkV);',
  '  } else {',
  // BAMBOO: smooth culm, hard node. The ring is the only strong mark
  // and the collar above it is paler.
  '    float bkNd = abs(fract(bkH * uBarkNode) - 0.5) * 2.0;',
  '    float bkRing = 1.0 - smoothstep(0.0, 0.10, bkNd);',
  '    bkCut = clamp(bkCut * 0.35 + bkRing, 0.0, 1.0);',
  '    bkTint = mix(bkTint * vec3(1.10, 1.14, 0.86), bkTint,',
  '                 smoothstep(0.08, 0.40, bkNd));',
  '  }',
  // Relief, not paint: the object-space gradient of the fissure depth
  // tilts the normal this patch cannot reach, and the sun answers.
  '  vec3 bkPx = dFdx(vAstraBarkP), bkPy = dFdy(vAstraBarkP);',
  '  vec3 bkGr = (bkPx * dFdx(bkCut) + bkPy * dFdy(bkCut))',
  '            / max(dot(bkPx, bkPx) + dot(bkPy, bkPy), 1e-12);',
  '  vec3 bkNr = normalize(vAstraBarkN);',
  '  vec3 bkSun = normalize(vAstraBarkS);',
  '  float bkRel = dot(normalize(bkNr + bkGr * uBarkDepth * bkSc), bkSun)',
  '              - dot(bkNr, bkSun);',
  // The floor is 0.58, not 0.45: nothing outdoors is lit by the sun
  // alone, and a relief that can take a facet to 45% of its own albedo
  // paints black holes where a fissure turns away.
  '  float bkLit = clamp(1.0 + bkRel * 0.9 + bkFlat * 0.10, 0.58, 1.6);',
  // The albedo underneath is kept as LUMINANCE — a trunk you coloured
  // dark stays dark, and every patch already on this material shows
  // through. Never an assignment.
  '  float bkLum = dot(diffuseColor.rgb, vec3(0.3333));',
  '  vec3 bkBase = bkTint * (0.75 + 0.55 * smoothstep(0.02, 0.35, bkLum));',
  '  diffuseColor.rgb = mix(diffuseColor.rgb, bkBase, 0.72);',
  // The ridge is lifted as far as the fissure is cut, or a barked trunk
  // is simply a darker trunk than the one it replaced. The FLOOR is
  // 0.72 of the depth asked for and never zero: a fissure still sees
  // the sky, and a black one is the one thing that reads as paint
  // rather than as relief.
  '  diffuseColor.rgb *= mix(1.0 + 0.30 * uBarkDepth,',
  '                          1.0 - 0.72 * uBarkDepth, bkCut);',
  // And what reaches the bottom of a fissure is SKY, not sun: the cut
  // is cooler than the ridge beside it, which is the depth cue that
  // survives being resolved down to four pixels.
  '  diffuseColor.rgb *= mix(vec3(1.0), vec3(0.88, 0.95, 1.10),',
  '                          bkCut * 0.65);',
  '  diffuseColor.rgb *= bkLit;',
  '  float bkMs = uBarkMoss * bkCut * smoothstep(0.30, 0.75,',
  '      astraBarkFbm(bkQ * 0.30, bkH * 0.30 + 2.9));',
  // Moss takes the trunk's own drift: one flat green cushion is the
  // giveaway that a bark is a texture and not a place things grow.
  '  diffuseColor.rgb = mix(diffuseColor.rgb,',
  '      uBarkMossColor * (1.0 + bkDr * 0.90), clamp(bkMs, 0.0, 1.0));',
].join('\n');

/**
 * Give a trunk bark that runs UP it — the cue a flat brown cylinder
 * misses.
 *
 * A trunk is the one surface in an outdoor scene a camera gets close
 * enough to read, and what it reads is direction: fissures many times
 * longer than they are wide, running along the grain. This samples its
 * field with the along-axis coordinate divided by `aniso` (5.5 to 12,
 * by kind), so the features are stretched up the trunk before the kind's
 * own structure is cut into them — plates for pine, papery lenticel
 * bands for birch, deep ridges for oak, smooth nodes for bamboo. All
 * four are ONE program: the kind is a uniform, so a stand of four
 * species costs one compile.
 *
 * The field is shaded from the trunk's OWN frame (object space, `axis`
 * default +Y), so it wraps a cylinder correctly wherever the tree is
 * placed and does not swim when the tree moves. A fallen log or a branch
 * needs its own `axis`.
 *
 * It BLENDS over the albedo: the kind's tint is mixed 0.72 over what is
 * there, weighted by that albedo's own luminance, then the fissures
 * multiply it down — so a dark trunk stays dark and any patch already on
 * the material shows through. It never assigns `diffuseColor.rgb`.
 * Roughness is per MATERIAL, not per pixel (`composeRoughness`): bark is
 * matte, bamboo is not.
 *
 * The one thing a fragment patch cannot reach is the normal, so the
 * relief is baked into the albedo instead — the object-space gradient of
 * the fissure depth against `sun`. It fades out on its own once a pixel
 * spans a fissure, which is why the trunk does not sparkle at distance.
 *
 * @param {THREE.Material} material Trunk material, patched in place — a
 *   shared material from `materials.js` barks every mesh wearing it, so
 *   clone it first (and clone BEFORE patching).
 * @param {object} [opts] `kind` 'pine' | 'birch' | 'oak' (default) |
 *   'bamboo'; `scale` metres across one fissure (default by kind,
 *   0.075..0.16 — this is the width of a crack, not the trunk);
 *   `depth` 0..1 how far the fissures cut, in shade and in relief
 *   (default 0.55); `mossy` 0..1 green held in the fissures (default
 *   0.12 — `damp.js`'s `patchMoss` grows the cushion on the shaded
 *   side, this is only what lodges in the cracks); `axis` the trunk's
 *   own axis in OBJECT space, THREE.Vector3 or [x, y, z] (default +Y);
 *   `tint` THREE.Color or hex, the bark colour (default by kind);
 *   `sun` world direction TOWARD the sun, for the relief (default a day
 *   sun); `seed` moves the field (default 1).
 * @returns {THREE.Material} The same material, uniforms live on
 *   `material.userData.uniforms`.
 */
export function patchBark(material, opts = {}) {
  const kind = BARK_KINDS[opts.kind] || BARK_KINDS.oak;
  const scale = Math.max(1e-3,
                         opts.scale === undefined ? kind.scale : opts.scale);
  const depth = unit(opts.depth === undefined ? 0.55 : opts.depth);
  const mossy = unit(opts.mossy === undefined ? 0.12 : opts.mossy);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const axis = readVec3(opts.axis, 0, 1, 0);
  if (axis.lengthSq() < 1e-9) axis.set(0, 1, 0);
  // A node count in FIELD units: the along-axis coordinate is already
  // divided by scale * aniso, so a spacing in metres has to come back
  // through the same divide or `scale` would move the nodes.
  const node = kind.node > 0
      ? (scale * kind.aniso) / kind.node : 0;
  composeRoughness(material, 'woodland:bark',
                   1 + (kind.rough - 1) * (0.4 + 0.6 * depth));
  return patchStandard(material, {
    name: 'woodland:bark',
    uniforms: {
      uBarkKind: { value: kind.id },
      uBarkScale: { value: scale },
      uBarkAniso: { value: kind.aniso },
      uBarkDepth: { value: depth },
      uBarkMoss: { value: mossy },
      uBarkCrack: { value: new THREE.Vector2(kind.crack[0], kind.crack[1]) },
      uBarkPlate: { value: new THREE.Vector2(kind.plate[0], kind.plate[1]) },
      uBarkNode: { value: node },
      uBarkAxis: { value: axis.normalize() },
      uBarkTint: { value: toColor(opts.tint, kind.tint) },
      uBarkMossColor: { value: toColor(opts.mossColor, 0x46551f) },
      uBarkSun: { value: readVec3(opts.sun, 0.45, 0.78, 0.35).normalize() },
      uBarkSeed: { value: seedOffset(seed) },
    },
    vertexHead: BARK_VERTEX_HEAD,
    vertexBody: [
      '  vAstraBarkP = transformed;',
      '  vAstraBarkN = normal;',
      '  vAstraBarkS = astraBarkLocalDir(uBarkSun);',
    ].join('\n'),
    fragmentHead: BARK_FRAGMENT_HEAD,
    fragmentBody: BARK_FRAGMENT_BODY,
  });
}

/**
 * The four silhouettes, and what each is worth as a card.
 *
 * `cross` is whether the stand-in gets two fixed quads at right angles
 * (which hold still while the camera moves) or one that turns to face
 * it. A tree is watched from a moving camera; a distant figure is not.
 */
const IMPOSTER_KINDS = {
  tree: { id: 0, cross: true, height: 9, aspect: 0.92, hue: 0.13,
          color: 0x4a5c26, second: 0x8a7050, sway: 0.020,
          bow: [1.9, 1.6, 0.20, 0.15], mid: 0.66 },
  conifer: { id: 1, cross: true, height: 14, aspect: 0.52, hue: 0.10,
             color: 0x2f4429, second: 0x7a6244, sway: 0.012,
             bow: [1.3, 1.0, 0.25, 0.32], mid: 0.50 },
  person: { id: 2, cross: false, height: 1.75, aspect: 0.46, hue: 0.55,
            color: 0x9c6a55, second: 0x40465a, sway: 0,
            bow: [1.1, 0.2, 0.90, 0.30], mid: 0.55 },
  building: { id: 3, cross: true, height: 11, aspect: 1.35, hue: 0.06,
              color: 0xa89880, second: 0x3a3630, sway: 0,
              bow: [0.5, 0.1, 1.40, 0.20], mid: 0.50 },
};

// The fallback irradiance, matched to THIS renderer's own daylight rig
// (`environment.js` sunRig 'day': a 0xfff0d8 sun at 5.4 and a 1.4
// hemisphere, at exposure 1.0 with no post chain).  The reference's
// 3.0 / 0.85 was graded for a different exposure and a bloom pass, and
// it lands a card field visibly under the geometry standing in it.
// Pass `rig` and none of this is guesswork.
// Measured off sunRig('day') here: the hemisphere delivers (0.42, 0.50,
// 0.71) of irradiance and the environment bake a further (1.25, 1.68,
// 2.11). Linear working values, not a hex — an irradiance over 1 has no
// hex to be written as.
const DAY_SUN = new THREE.Color(0xfff0d8).multiplyScalar(5.4);
const DAY_AMBIENT = new THREE.Color(1.67, 2.18, 2.82);

/**
 * The irradiance a diffuse surface takes from an equirect environment.
 *
 * `sunRig` bakes its environment as a linear half-float equirect of
 * RADIANCE, and every lit material in the scene is receiving it through
 * three's PMREM. A card is not, so without this the far field is lit by
 * the hemisphere alone and lands under the geometry standing in it — by
 * a factor that is not a constant, because the ratio of sky bake to
 * hemisphere is completely different at noon and at midnight (measured
 * here: a treeline tuned to match by day came out at 0.72 of the real
 * crown beside it under the moon).
 *
 * Solid-angle weighted mean radiance over the sphere, times PI. Sampled
 * on a 64 x 32 grid, which is 2 048 texels of a 256 x 128 bake.
 *
 * @returns {THREE.Color|null} null for a texture with no readable data
 *   (a PMREM render target, a loaded image), which is not an error —
 *   the caller falls back to the hemisphere alone.
 */
function envIrradiance(tex) {
  const img = tex && tex.image;
  const data = img && img.data;
  if (!data || !img.width || !img.height) return null;
  const w = img.width, h = img.height;
  const half = tex.type === THREE.HalfFloatType;
  const dy = Math.max(1, Math.round(h / 32));
  const dx = Math.max(1, Math.round(w / 64));
  let r = 0, g = 0, b = 0, sum = 0;
  for (let y = 0; y < h; y += dy) {
    // Row 0 is the nadir, as the bake writes it.
    const cw = Math.cos(((y + 0.5) / h - 0.5) * Math.PI);
    for (let x = 0; x < w; x += dx) {
      const i = (y * w + x) * 4;
      const f = half ? THREE.DataUtils.fromHalfFloat : (v) => v;
      r += f(data[i]) * cw;
      g += f(data[i + 1]) * cw;
      b += f(data[i + 2]) * cw;
      sum += cw;
    }
  }
  if (sum <= 0) return null;
  return new THREE.Color(r / sum, g / sum, b / sum)
      .multiplyScalar(Math.PI);
}

/**
 * Read a `sunRig()` package as the three numbers a card needs.
 *
 * A card does its own lighting — it has no normals three can light and
 * no place in the shadow map — so the ONE thing that makes it belong is
 * being handed the same irradiance the geometry beside it receives:
 * the directional as irradiance (colour times intensity), and the
 * indirect as hemisphere PLUS the baked environment, which is what
 * three gives every lit mesh in the scene.
 */
function rigLight(rig) {
  if (!rig || !rig.sun || !rig.fill) return null;
  // The hemisphere at the mix a roughly upright surface sees.
  const amb = rig.fill.color.clone()
      .lerp(rig.fill.groundColor, 0.45)
      .multiplyScalar(rig.fill.intensity);
  const env = envIrradiance(rig.envTex);
  if (env) amb.add(env);
  return {
    sunDir: (rig.sunDir ? rig.sunDir.clone()
                        : rig.sun.position.clone()).normalize(),
    sunColor: rig.sun.color.clone().multiplyScalar(rig.sun.intensity),
    ambient: amb,
  };
}

/**
 * Stand-ins on cards: a forest, a crowd or a town for one draw call.
 *
 * Use when each crown spans roughly 60 screen pixels or fewer. The older
 * ~35 m guideline was measured at 512 px and is not resolution-independent.
 * Measured against the same stand built
 * as real geometry (mean absolute pixel difference over the frame,
 * 512 px, daylight): 15 m 12.6/255 and plainly flat — the crown's
 * silhouette is right but it has no interior and the eye reads a
 * cut-out; 40 m 4.1/255, which is the point where a still frame stops
 * telling them apart; 90 m 1.4/255, indistinguishable. So put real
 * geometry in the near field and hand the rest to this — and give the
 * two the same species colours, because the join is where a mismatch
 * shows.
 *
 * Every card is seated on `heightAt` with its BASE at the ground, is
 * scaled and hue-shifted per instance (a field of identical cards is
 * wallpaper at any distance), and — for `tree` and `conifer` — leans on
 * `grass.js`'s `windOf`, so one `wind` option moves the meadow, the
 * reeds and the far treeline together.
 *
 * The whole field is ONE `InstancedBufferGeometry`: `position` stays at
 * zero (the GTAO reason `instancedQuad` documents) and the cards are
 * built in the vertex shader, so the stated bounding SPHERE is the only
 * thing three can cull the field by. It is computed from the real
 * placements here. The mesh is guarded by `keepOutOfDepthPasses`,
 * because an override material draws a transparent card as a solid wall
 * — which also means these cards cast no shadow; their contact with the
 * ground is the darkened foot in the silhouette itself.
 *
 * @param {object} [opts] `source` 'tree' (default) | 'conifer' |
 *   'person' | 'building'; `count` stand-ins (default 120 — one draw
 *   call at any count); `extent` metres of the square field's side,
 *   centred on the group's origin (default 120); `height` metres of the
 *   average stand-in (default by source: 9 / 14 / 1.75 / 11);
 *   `heightAt` (x, z) => y, the ground the cards are seated on (default
 *   y = 0); `seed` PRNG seed (default 7); `cross` two fixed quads at
 *   right angles instead of one turning card (default true except for
 *   `person`); `color` the main albedo, `trunkColor` the second one
 *   (trunk, clothing, window); `rig` the `sunRig()` package the scene is
 *   lit by — PASS IT: a card does its own lighting, so this is the whole
 *   of what makes the far field belong to the near one, and it is what
 *   turns a treeline down at night; `sunDir`/`sunColor`/`ambient` the
 *   same three by hand, each overriding `rig` (defaults: this
 *   renderer's own daylight rig); `wind` as `grass.js`'s `windOf` reads
 *   it; `name` group name.
 * @returns {THREE.Group} Named `Imposters`, resting on the ground,
 *   holding ONE mesh, with `userData.tick(t)` driving the lean. Add it
 *   at the scene ROOT: the cards billboard against WORLD axes.
 */
export function makeImposters(opts = {}) {
  const kind = IMPOSTER_KINDS[opts.source] || IMPOSTER_KINDS.tree;
  const count = Math.max(1, Math.round(
      opts.count === undefined ? 120 : opts.count));
  const extent = Math.max(1, opts.extent === undefined ? 120 : opts.extent);
  const height = Math.max(0.05,
                          opts.height === undefined ? kind.height : opts.height);
  const ground = typeof opts.heightAt === 'function' ? opts.heightAt : null;
  const seed = opts.seed === undefined ? 7 : opts.seed;
  const cross = opts.cross === undefined ? kind.cross : !!opts.cross;
  const wind = windOf(opts.wind);

  const field = plant(count, extent, seed, kind, ground);
  const cards = cross ? count * 2 : count;
  const width = height * kind.aspect;
  const half = 0.5 * width * field.scale * (cross ? Math.SQRT2 : 1)
      + Math.abs(kind.sway * wind.amp * 2) * height;
  const box = new THREE.Box3(
      new THREE.Vector3(-extent / 2 - half, field.low, -extent / 2 - half),
      new THREE.Vector3(extent / 2 + half, field.high + height * field.scale,
                        extent / 2 + half));
  const sphere = box.getBoundingSphere(new THREE.Sphere());

  // The REAL radius: `position` is all zeros, so three has nothing else
  // to cull by and the 10 km default would frame 10 km of empty air.
  const geom = instancedQuad(cards, 1, 1, sphere.radius);
  geom.setAttribute('aPos', new THREE.InstancedBufferAttribute(
      cardArray(field.pos, cross, 3), 3));
  geom.setAttribute('aCard', new THREE.InstancedBufferAttribute(
      cardArray(field.card, cross, 4), 4));
  if (cross) {
    const yaw = geom.attributes.aCard.array;
    for (let i = 0; i < count; i++) yaw[(i * 2 + 1) * 4] += Math.PI / 2;
  }
  geom.boundingBox = box;
  // instancedQuad centres its sphere on the ORIGIN; this one is centred
  // on the field, so it is tighter and it is what three culls by. The
  // radius passed above is only the floor under it.
  geom.boundingSphere = sphere;

  const light = rigLight(opts.rig);
  const mesh = new THREE.Mesh(geom, imposterMaterial({
    kind, width, height, cross, wind,
    color: toColor(opts.color, kind.color),
    second: toColor(opts.trunkColor, kind.second),
    sun: opts.sunDir === undefined && light
        ? light.sunDir : readVec3(opts.sunDir, 0.45, 0.78, 0.35).normalize(),
    // Irradiance, not a screen colour.
    sunColor: opts.sunColor === undefined
        ? (light ? light.sunColor : DAY_SUN.clone())
        : toColor(opts.sunColor, 0xffffff),
    ambient: opts.ambient === undefined
        ? (light ? light.ambient : DAY_AMBIENT.clone())
        : toColor(opts.ambient, 0xffffff),
  }));
  mesh.name = 'ImposterCards';
  const g = new THREE.Group();
  g.name = opts.name || 'Imposters';
  g.add(keepOutOfDepthPasses(mesh));
  g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
  return attachDisposal(g, snapshotResources(g));
}

/**
 * Seat `count` stand-ins on a jittered grid over the field.
 *
 * The grid is shuffled before it is cut to `count`, or a field that does
 * not fill its last row loses a rectangular corner of itself.
 */
function plant(count, extent, seed, kind, ground) {
  const cells = Math.max(1, Math.ceil(Math.sqrt(count)));
  const step = extent / cells;
  const rand = mulberry32(seed);
  const order = [];
  for (let i = 0; i < cells * cells; i++) order.push(i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const t = order[i]; order[i] = order[j]; order[j] = t;
  }
  const pos = new Float32Array(count * 3);
  const card = new Float32Array(count * 4);
  let low = Infinity, high = -Infinity, scale = 0;
  for (let i = 0; i < count; i++) {
    const c = order[i % order.length];
    const x = -extent / 2 + ((c % cells) + rand()) * step;
    const z = -extent / 2 + (Math.floor(c / cells) + rand()) * step;
    const y = ground ? ground(x, z) : 0;
    pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z;
    const s = 0.72 + 0.62 * rand() * rand();
    card[i * 4] = rand() * _TAU;
    card[i * 4 + 1] = s;
    card[i * 4 + 2] = (rand() * 2 - 1) * kind.hue;
    card[i * 4 + 3] = rand();
    low = Math.min(low, y); high = Math.max(high, y);
    scale = Math.max(scale, s);
  }
  return { pos, card, low, high, scale };
}

/** Repeat each stand-in's data once per card it gets. */
function cardArray(src, cross, stride) {
  if (!cross) return src;
  const out = new Float32Array(src.length * 2);
  for (let i = 0; i < src.length / stride; i++) {
    for (let k = 0; k < stride; k++) {
      out[i * 2 * stride + k] = src[i * stride + k];
      out[(i * 2 + 1) * stride + k] = src[i * stride + k];
    }
  }
  return out;
}

/** Card, silhouette and light — the whole stand-in, in one program. */
function imposterMaterial(cfg) {
  return makeShaderMaterial({
    name: 'Imposters',
    uniforms: {
      uImpKind: { value: cfg.kind.id },
      uImpSize: { value: new THREE.Vector2(cfg.width, cfg.height) },
      uImpFace: { value: cfg.cross ? 0 : 1 },
      uImpSway: { value: cfg.kind.sway * cfg.wind.amp * 2 },
      uImpWind: { value: cfg.wind.dir.clone() },
      uImpSpeed: { value: cfg.wind.speed },
      uImpColor: { value: cfg.color },
      uImpSecond: { value: cfg.second },
      uImpSun: { value: cfg.sun },
      uImpBow: { value: new THREE.Vector4(
          cfg.kind.bow[0], cfg.kind.bow[1], cfg.kind.bow[2],
          cfg.kind.bow[3]) },
      uImpMid: { value: cfg.kind.mid },
      uImpSunColor: { value: cfg.sunColor },
      uImpAmbient: { value: cfg.ambient },
    },
    varyings: 'varying vec2 vImpUv; varying vec4 vImpVar;'
        + ' varying vec3 vImpN;',
    vertexHead: [
      'attribute vec3 aCorner;',
      'attribute vec3 aPos;',
      'attribute vec4 aCard;',
      'uniform vec2 uImpSize;',
      'uniform float uImpFace;',
      'uniform float uImpSway;',
      'uniform float uImpSpeed;',
      'uniform vec2 uImpWind;',
      'uniform vec4 uImpBow;',
      'uniform float uImpMid;',
      // The sun vector is a FRAGMENT uniform in the reference; the
      // front/back test below needs it here too, and dedupeUniforms
      // keeps the two declarations from colliding.
      'uniform vec3 uImpSun;',
    ].join('\n'),
    vertexMain: [
      '  vImpUv = uv;',
      // Camera axes in LOCAL space (rows of modelViewMatrix), so a
      // moved or rotated parent cannot tear the field off the camera.
      '  vec3 camR = vec3(modelViewMatrix[0][0], modelViewMatrix[1][0],',
      '                   modelViewMatrix[2][0]);',
      '  vec3 up = vec3(0.0, 1.0, 0.0);',
      // A stand-in yaws, it never tips: a card that also pitches with
      // the camera lifts a whole forest off the ground together.
      '  vec3 faceR = normalize(camR - up * dot(camR, up)',
      '                         + vec3(1e-5, 0.0, 0.0));',
      '  vec3 crossR = vec3(cos(aCard.x), 0.0, sin(aCard.x));',
      '  vec3 right = normalize(mix(crossR, faceR, uImpFace));',
      '  float hf = aCorner.y + 0.5;',
      '  vec3 p = aPos + right * (aCorner.x * uImpSize.x * aCard.y)',
      '         + up * (hf * uImpSize.y * aCard.y);',
      // One wind for the whole scene (grass.js windOf), and only the
      // top of the stand-in answers it.
      '  float sw = uTime * uImpSpeed + astraStagger(aPos.x + aPos.z * 0.31);',
      '  p.xz += uImpWind * (uImpSway * uImpSize.y * hf * hf * sin(sw));',
      '  transformed = p;',
      // A crown is round, and the two quads of a cross have to agree
      // about that or the seam between them lights as a hard edge: the
      // fake normal is RADIAL from the mass centre, not the card's own.
      '  vec3 fwd = cross(right, up);',
      '  vImpN = right * (aCorner.x * uImpBow.x)',
      '        + up * ((hf - uImpMid) * uImpBow.y + uImpBow.w)',
      '        + fwd * uImpBow.z;',
      '  vImpN = astraNormalTransform(mat3(modelMatrix), vImpN);',
      // The other quad of a cross is edge-on exactly when this one is
      // square to the camera, and an edge-on card is a lit 1 px sliver
      // down the middle of the crown. Fade it; nothing is lost.
      '  vec3 camF = vec3(modelViewMatrix[0][2], modelViewMatrix[1][2],',
      '                   modelViewMatrix[2][2]);',
      // WHICH SIDE OF THE MASS THE SUN IS ON, in world space, so it is
      // the same answer for both quads of a cross and for a card that
      // turns. +1 the sun is behind the camera (the visible half is the
      // lit half); -1 the stand is between the camera and the sun and
      // the visible half is in its own shadow, transmitting.
      '  vec3 wPos = (modelMatrix * vec4(transformed, 1.0)).xyz;',
      '  float sunSide = dot(uImpSun,',
      '                      normalize(cameraPosition - wPos));',
      '  vImpVar = vec4(aCard.z, aCard.w,',
      '      mix(smoothstep(0.10, 0.42, abs(dot(fwd, camF))), 1.0,',
      '          uImpFace), sunSide);',
    ].join('\n'),
    fragmentHead: [
      'uniform float uImpKind;',
      'uniform vec3 uImpColor;',
      'uniform vec3 uImpSecond;',
      'uniform vec3 uImpSunColor;',
      'uniform vec3 uImpAmbient;',
      'uniform vec3 uImpSun;',
    ].join('\n'),
    fragmentMain: [
      '  vec2 iq = vec2(vImpUv.x * 2.0 - 1.0, vImpUv.y);',
      '  float iPh = vImpVar.y * 37.0;',
      '  float iS = -1.0;',
      '  vec3 iAlb = uImpColor;',
      '  if (uImpKind < 0.5) {',
      // A broadleaf is one ragged mass on a short stem, and the edge is
      // where the eye decides tree or lollipop.
      '    float tLean = (fract(iPh * 0.73) - 0.5) * 0.20 * iq.y;',
      '    float tWide = 0.76 + 0.18 * fract(iPh * 0.41);',
      '    vec2 tD = vec2((iq.x - tLean) / tWide,',
      '      (iq.y - 0.64 - 0.025 * sin(iPh)) / (0.31 + 0.035 * fract(iPh)));',
      '    float tR = length(tD);',
      '    tR += 0.48 * (astraFbm2(vec2(atan(tD.y, tD.x) * 1.7, iPh), 3)',
      '                  - 0.44);',
      '    float tCrown = 0.92 - tR;',
      // Foliage has gaps between sprays and a porous outline. Filtering the
      // finest field keeps a small distant crown stable rather than stippled.
      '    vec2 tLeafP = iq * vec2(39.0, 52.0) + iPh;',
      '    float tFootprint = max(length(dFdx(tLeafP)), length(dFdy(tLeafP)));',
      '    float tLeaf = mix(astraFbm2(tLeafP, 3), 0.44, smoothstep(0.7, 2.0, tFootprint));',
      '    float tClump = astraFbm2(iq * vec2(13.0, 19.0) + iPh * 1.3, 3);',
      '    tCrown -= (0.50 - tClump) * 0.36 + (0.46 - tLeaf) * 0.22;',
      '    float tGaps = tLeaf - mix(0.17, 0.33, smoothstep(0.28, 0.02, tCrown));',
      '    tCrown = min(tCrown, tGaps);',
      '    iS = max(tCrown, min(0.026 - abs(iq.x - tLean * iq.y), 0.70 - iq.y));',
      '    iAlb = mix(uImpSecond, uImpColor * (0.66 + 0.60 * iq.y),',
      '               smoothstep(-0.02, 0.10, tCrown));',
      '  } else if (uImpKind < 1.5) {',
      // A spire that steps out at every whorl is a conifer; a clean
      // triangle is a traffic cone.
      '    float cW = 0.80 * pow(max(1.0 - iq.y, 0.0), 0.80);',
      '    cW *= 0.78 + 0.34 * abs(fract(iq.y * 6.0 + iPh) - 0.5) * 2.0;',
      '    cW -= 0.10 * astraFbm2(vec2(iq.y * 22.0, iPh), 2);',
      '    iS = max(cW - abs(iq.x), min(0.035 - abs(iq.x), 0.14 - iq.y));',
      '    iAlb = mix(uImpSecond, uImpColor * (0.60 + 0.62 * iq.y),',
      '               smoothstep(0.10, 0.20, iq.y));',
      '  } else if (uImpKind < 2.5) {',
      // Head, shoulders and the gap between the legs. iq.x spans HALF
      // the card and iq.y the whole height, so a round head is an
      // ellipse here.
      '    float pW = 0.60 - 0.17 * smoothstep(0.80, 0.90, iq.y)',
      '             - 0.17 * smoothstep(0.74, 0.52, iq.y)',
      '             + 0.07 * smoothstep(0.52, 0.34, iq.y);',
      '    float pBody = min(min(pW - abs(iq.x), 0.90 - iq.y), iq.y);',
      '    iS = max(0.29 - length(vec2(iq.x, (iq.y - 0.915) * 4.8)),',
      '             min(pBody, max(abs(iq.x) - 0.11, iq.y - 0.38)));',
      '    iAlb = mix(uImpSecond, uImpColor,',
      '               smoothstep(0.38, 0.46, iq.y));',
      '    iAlb = mix(iAlb, uImpColor * 1.35,',
      '               smoothstep(0.85, 0.90, iq.y));',
      '  } else {',
      // A roofline that steps per instance, and windows that vanish on
      // their own once a pixel spans one (astraStroke).
      '    float bTop = 0.60 + 0.36 * fract(iPh * 3.7);',
      '    float bW = 0.34 + 0.16 * fract(iPh * 7.3);',
      '    iS = min(bW - abs(iq.x), bTop - iq.y);',
      '    float bWin = astraStroke(iq.x * 7.0 + 0.5, 0.26)',
      '               * astraStroke(iq.y * 13.0, 0.24)',
      '               * step(iq.y, bTop - 0.06);',
      '    iAlb = mix(uImpColor * (0.80 + 0.30 * iq.y), uImpSecond,',
      '               bWin * 0.85);',
      '  }',
      // A card is antialiased by its own gradient or a distant stand
      // stipples in and out of the frame as the camera turns.
      '  float iAA = fwidth(iS) + 1e-4;',
      '  float iA = clamp(iS / iAA + 0.5, 0.0, 1.0);',
      // Dropped whole, never faded: a card that still writes depth at
      // 5% alpha punches a sky-coloured hole through the one behind it.
      '  if (iA < 0.5 || vImpVar.z < 0.5) discard;',
      // LEAVES, NOT FELT. A crown is clumps with light between them,
      // and one flat mass is what makes a card read as a cut-out at any
      // distance. The field is in card space at the instance's own
      // phase, so no two stand-ins clump alike.
      '  float iLeaf = uImpKind < 1.5 ? 1.0 : 0.0;',
      '  float iMass = astraFbm2(vec2(iq.x * 5.2, vImpUv.y * 5.2)',
      '                          + iPh, 3);',
      '  iAlb *= 1.0 + iLeaf * (iMass - 0.375) * 1.15;',
      '  float iFine = astraFbm2(vec2(iq.x * 17.0, vImpUv.y * 17.0)',
      '                          + iPh * 1.7, 2);',
      '  iAlb *= 1.0 + iLeaf * (iFine - 0.375) * 0.85;',
      '  vec2 iSprayP = iq * vec2(55.0, 72.0) + iPh * 2.1;',
      '  float iSprayFootprint = max(length(dFdx(iSprayP)), length(dFdy(iSprayP)));',
      '  float iSpray = mix(astraNoise2(iSprayP), 0.5, smoothstep(0.6, 1.8, iSprayFootprint));',
      '  iAlb *= 1.0 + iLeaf * (iSpray - 0.5) * 0.60;',
      // Hue VARIANCE inside one crown, not only between crowns: the
      // sunlit leaves are yellow and the ones in the mass are
      // blue-green, and a ball of one hue is a painted ball.
      '  iAlb = astraHueBreak(iAlb, vec2(iq.x, vImpUv.y) + iPh, 3.3,',
      '                       mix(0.20, 0.62, iLeaf));',
      '  iAlb = astraHueShift(iAlb, vImpVar.x);',
      // The card is flat and the thing it stands for is not: the bowed
      // normal is what puts a lit side and a shaded side on it.
      '  vec3 iN = normalize(vImpN);',
      '  float iNdL = max(dot(iN, uImpSun), 0.0);',
      // A crown is a BALL, and the camera only ever sees one half of
      // it. The bowed normal knows nothing about that, so on a BACKLIT
      // stand it hands half of every card a full sun term the geometry
      // beside it does not get — measured on this renderer at 1.41x the
      // real crown's luminance, which is what a treeline pasted on with
      // scissors looks like. Fold the sun down as the mass turns away.
      '  iNdL *= mix(0.09, 1.0, smoothstep(-0.30, 0.45, vImpVar.w));',
      // What a backlit crown DOES do is glow: the sun comes through the
      // thin edge of the mass. Warm, weighted to the silhouette, and
      // gone the moment the sun is on the camera's side.
      '  float iEdge = 1.0 - smoothstep(0.0, 0.12, iS);',
      '  float iBack = smoothstep(0.15, -0.70, vImpVar.w);',
      // Broken by the same clump field, or the glow is a stroke drawn
      // round the silhouette rather than light coming through leaves.
      '  vec3 iTrans = uImpSunColor * vec3(1.0, 0.82, 0.44)',
      '              * (0.065 * iLeaf * iEdge * iBack',
      '                 * smoothstep(0.30, 0.52, iMass) * (0.25 + 0.75 * iFine));',
      // A CROWN OCCLUDES ITSELF. Nothing else here can supply that — a
      // card has no ambient occlusion, no self-shadow and no place in
      // the shadow map — and handed the TRUE environment irradiance
      // (the one every lit mesh in the scene receives) an unoccluded
      // mass reads as a pale green cloud: measured 1.57x the real crown
      // standing in the same field. The deep interior of a crown sees a
      // fraction of the sky its outer leaves do. Floored, because that
      // fraction is not zero: what bounced off the ground and off the
      // rest of the wood gets in whatever the leaves do.
      '  float iDeep = smoothstep(-0.02, 0.20, iS);',
      '  float iAO = max(0.26,',
      '      0.60 - iLeaf * 0.34 * iDeep * (1.0 - 0.78 * iMass));',
      // Irradiance over PI, the same arithmetic a built-in Lambert
      // does, so a card sits at the brightness its geometry would.
      // A crown's underside sees less sky than its top, and that
      // gradient is most of what says "mass" rather than "cut-out".
      '  vec3 iIrr = (uImpAmbient * iAO',
      '               + uImpSunColor * iNdL * mix(1.0, iAO, 0.75) + iTrans)',
      '            * mix(0.62, 1.06, smoothstep(0.0, 0.85, iq.y));',
      '  gl_FragColor = vec4(iAlb * iIrr / PI, 1.0);',
    ].join('\n'),
    // Opaque clipping avoids partial-alpha halos between unsorted cards.
    // The host post chain handles the silhouette's screen-space antialiasing.
    transparent: false,
    alphaToCoverage: false,
    depthWrite: true,
    side: THREE.DoubleSide,
  });
}
