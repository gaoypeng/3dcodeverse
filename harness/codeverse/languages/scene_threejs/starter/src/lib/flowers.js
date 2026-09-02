/**
 * Flowers: the colour a meadow carries above the grass, and the petals
 * and leaves that come down out of the air.
 *
 * `makeFlowers` seats flowering plants on the caller's ground and nods
 * their heads in the SAME wind grass.js rides — `windOf` is imported
 * rather than re-read, so one scene's meadow and its flowers lean and
 * gust together. A petal is a thin double-sided card whose rim is
 * translucent and which is lit THROUGH — by `patchLeafSSS` on the
 * plants, whose sun is stated, and by `patchTranslucency` on what is
 * in the air, which takes its light from the scene's own lamps; the
 * silhouette (a disc of petals, a cup, a spike) is a set of UNIFORMS
 * and never baked GLSL, because `patchStandard` hands one compiled
 * program to every material that shares a patch name.
 *
 * `makeFalling` is the other half: leaves or petals drifting DOWN
 * through the air, each at its own rate, tumbling about its own axis
 * and sliding sideways as it goes, because a leaf does not fall
 * straight. The whole field wraps inside the volume it advertises, so
 * the box it states is the box it occupies.
 *
 * Both are ONE draw call whose every moving part lives in the vertex
 * shader, both are deterministic in `seed`, and both need driving from
 * your `tick()` — `obj.userData.tick(t)` — or nothing moves.
 */

import * as THREE from 'three';

import { windOf } from './grass.js';
import { patchTranslucency } from './finish.js';
import { patchLeafSSS } from './foliage_shade.js';
import { mulberry32 } from './noise.js';
import {
  instancedQuad, keepOutOfDepthPasses, patchStandard, shadowLike,
  tickShaders,
} from './shader.js';

const _TAU = Math.PI * 2;
// Rows up a stem: 3 is where the bend of a 30 cm stalk stops faceting.
const _STEM_ROWS = 3;
// The most arc a stem is laid over by, and the largest the gust term
// (0.25 + 1.25 * fbm) * (0.62 + 0.38 * sway) can reach. Both are read
// by the CPU to state a box the shader cannot leave.
const _MAX_BEND = 1.2;
const _PUSH_MAX = 1.19;

/**
 * One row per silhouette. These numbers ARE the difference between the
 * three flowers: `elev` lifts a petal off the head plane (flat disc,
 * upright cup), `spike` spreads the florets DOWN the stem instead of
 * gathering them at the tip, and `prof` is the petal's outline.
 *
 * The colour rows are the OTHER half. A patch needs a spread of petal
 * colour or it reads as one moulded part, but that spread has to stay
 * inside the species: `sib` is the sibling albedo, stated as an HSL
 * offset from whatever petal colour the caller ends up with (so it
 * follows `opts.color` too), and `hue` is how far a single plant may
 * rotate off the line between them. `astraHueShift` is an RGB-space
 * rotation about the grey axis: on a near-primary it drives a channel
 * NEGATIVE and the clamp turns the swing into a fork, so a saturated
 * kind gets a SMALL rotation and takes its variety from `sib` instead
 * (measured: 0.25 rad on the poppy split the field into two lobes,
 * amber at 20-40 deg and magenta at 330-355 deg, with nothing between).
 */
const _KINDS = {
  daisy: {
    petals: 13, cols: 1, rows: 3, step: _TAU / 13,
    elev: 0.17, curl: -0.12, cup: 0.14, spike: 0,
    len: 0.055, wid: 0.022, disc: 0.017, bulge: 0.008,
    prof: [2.6, 0.40], blotch: 0,
    height: 0.32, petal: 0xdedad0, throat: 0xc9c3a6, disc_: 0xbe8f10,
    // Warm cream to a cool paper white: the swing a white flower
    // really carries, and safe to rotate because it is barely coloured.
    // The cool end drops SATURATION as it turns — a white that keeps
    // its saturation across half the wheel is a blue flower.
    sib: [0.42, -0.10, 0.015], hue: 0.30,
  },
  poppy: {
    petals: 5, cols: 2, rows: 3, step: _TAU / 5,
    elev: 0.74, curl: 0.34, cup: 0.60, spike: 0,
    len: 0.060, wid: 0.052, disc: 0.013, bulge: 0.010,
    prof: [1.6, 0.60], blotch: 0.8,
    height: 0.44, petal: 0xa2200c, throat: 0x4a1208, disc_: 0x2a1e12,
    // Scarlet to vermilion, the two ends of a field poppy — WARM, both
    // of them. The other way round the wheel is carmine, which the
    // pale transmitted lift then carries the rest of the way to pink.
    sib: [0.020, 0.02, 0.045], hue: 0.07,
  },
  lavender: {
    petals: 34, cols: 1, rows: 1, step: 2.39996,
    elev: 1.00, curl: 0.10, cup: 0.30, spike: 0.30,
    len: 0.026, wid: 0.019, disc: 0, bulge: 0,
    prof: [2.0, 0.50], blotch: 0,
    height: 0.52, petal: 0x8a72c8, throat: 0xa696d6, disc_: 0x6a5aa8,
    sib: [-0.035, 0.10, -0.10], hue: 0.16,
  },
};

/** The sibling albedo a kind spreads toward, from the caller's own. */
function siblingOf(color, kind) {
  return color.clone().offsetHSL(kind.sib[0], kind.sib[1], kind.sib[2]);
}

/**
 * A patch of flowering plants, rooted on the caller's ground.
 *
 * Buys the one thing a green meadow is missing: colour that stands
 * ABOVE the grass line and moves with it. Thin double-sided petals
 * with translucent rims, a stem that bends on an arc of its own
 * length, and a head that nods a beat behind the stem because a head
 * is heavy. Every plant differs — height, head size, how far it has
 * opened, hue, lean and the jitter of each petal — since a field of
 * identical flowers is a texture, not a field.
 *
 * @param {object} [opts]
 *   `extent` metres of the square patch's side (default 6, centred on
 *   the group's origin); `density` plants per square metre (default 9;
 *   `maxPlants` caps it, default 12000); `height` stem height in metres
 *   (default per kind, 0.32-0.52, varied per plant); `kind` 'daisy'
 *   (default, a flat disc of narrow petals round a boss), 'poppy' (a
 *   deep cup of five broad petals with a dark throat) or 'lavender' (a
 *   spike of florets running down the stem); `color` petal colour, an
 *   ALBEDO not a screen colour — a scene sun runs at 5-6, so a hex that
 *   already looks sunlit tone-maps to paper, and the patch spreads
 *   ITSELF either side of whatever you pass (each plant sits somewhere
 *   between it and a sibling the kind states as an HSL offset, then
 *   leans a few degrees more); `shadows` cast into the shadow map
 *   (default false — a head is smaller than a shadow texel on a rig
 *   fitted to a whole scene, so it lands as a block; turn it on for a
 *   sun rig fitted to the patch); `heightAt` (x, z) => y, the
 *   caller's ground height (default flat y = 0); `wind` grass.js's
 *   option in either spelling — a strength multiplier or
 *   `{dir, strength, speed}` — so one wind moves a scene's grass and its
 *   flowers together; `seed` PRNG seed (default 7); `sunDir`
 *   THREE.Vector3 toward the sun, for the light coming THROUGH a petal;
 *   `name` group name.
 * @returns {THREE.Group} Named `Flowers`, holding ONE instanced mesh
 *   `Plants` whose stated box is the box it occupies at any bend, with
 *   `userData.tick(t)` driving the wind.
 */
export function makeFlowers(opts = {}) {
  const kind = _KINDS[opts.kind] || _KINDS.daisy;
  const extent = Math.max(0.2, opts.extent === undefined ? 6 : opts.extent);
  const density = Math.max(0.01,
      opts.density === undefined ? 9 : opts.density);
  const height = Math.max(0.02,
      opts.height === undefined ? kind.height : opts.height);
  const maxPlants = opts.maxPlants === undefined ? 12000 : opts.maxPlants;
  const seed = opts.seed === undefined ? 7 : opts.seed;
  const ground = typeof opts.heightAt === 'function' ? opts.heightAt : null;
  const wind = windOf(opts.wind);
  const petal = new THREE.Color(
      opts.color === undefined ? kind.petal : opts.color);

  const field = plantField(
      extent, density, height, kind, wind, ground, seed, maxPlants);
  const g = new THREE.Group();
  g.name = opts.name || 'Flowers';
  g.add(plantMesh(field, kind, extent, petal, wind, opts.sunDir,
                  opts.shadows === true));
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

/**
 * Plant the patch, and state what it can reach.
 *
 * Reach is computed from the SAME arc the vertex shader draws, at the
 * largest bend the gust term can produce, so the stated box holds at
 * every t rather than at t = 0.
 */
function plantField(extent, density, height, kind, wind, ground, seed,
                    maxPlants) {
  const want = Math.max(
      1, Math.min(Math.round(density * extent * extent), maxPlants));
  const cells = Math.max(1, Math.round(Math.sqrt(want)));
  const step = extent / cells;
  const rand = mulberry32(seed);
  const pos = [], shape = [], bend = [], vary = [];
  let n = 0, reach = 0, low = Infinity, high = -Infinity;
  for (let iz = 0; iz < cells && n < maxPlants; iz++) {
    for (let ix = 0; ix < cells && n < maxPlants; ix++) {
      const x = -extent / 2 + (ix + rand()) * step;
      const z = -extent / 2 + (iz + rand()) * step;
      const y = ground ? ground(x, z) : 0;
      const h = height * (0.62 + 0.7 * rand());
      // Head size, and how far this one has OPENED: a patch where every
      // bloom is at full spread reads as plastic.
      const hs = 0.8 + 0.55 * rand();
      const open = 0.55 + 0.7 * rand();
      const stiff = (0.7 + 0.6 * rand()) * (0.55 + 0.6 * h / height);
      const lean = 0.05 + 0.16 * rand();
      pos.push(x, y, z);
      shape.push(h, height * (0.011 + 0.006 * rand()), hs, open);
      bend.push(wind.amp * stiff, rand() * _TAU, lean, rand() * _TAU);
      vary.push((rand() - 0.5) * kind.hue, rand() * _TAU, rand(),
                0.82 + 0.32 * rand());
      const k = Math.min(_MAX_BEND, lean + wind.amp * stiff * _PUSH_MAX);
      const head = headReach(kind, hs);
      reach = Math.max(reach, h * (1 - Math.cos(k)) / k + head);
      low = Math.min(low, y);
      high = Math.max(high, y + h + head);
      n++;
    }
  }
  return { n, pos, shape, bend, vary, reach,
           low: n ? low : 0, high: n ? high : 0 };
}

/** How far a head of scale `hs` sticks out past the stem's tip. */
function headReach(kind, hs) {
  const s = hs * 1.25;
  return Math.max(
      kind.disc * hs + kind.len * s * (1 + Math.abs(kind.curl))
          + kind.wid * s * (1 + Math.abs(kind.cup)),
      kind.disc * s * 1.5 + kind.bulge * s);
}

/**
 * One plant as base geometry: a stem strip, its petals, its boss.
 *
 * Every copy is built in the vertex shader from `aCorner` and `aPart`
 * (which part, which petal, how many), so the KIND lives in the layout
 * and in uniforms — never in the GLSL, which one program serves for
 * all three. `position` stays at zero for the GTAO reason
 * `instancedQuad` documents.
 */
function plantLattice(kind, count) {
  const pieces = [];
  const stem = new THREE.PlaneGeometry(1, 1, 1, _STEM_ROWS);
  stem.translate(0, 0.5, 0);
  pieces.push([stem, 0, 0, 1]);
  for (let i = 0; i < kind.petals; i++) {
    pieces.push([new THREE.PlaneGeometry(1, 1, kind.cols, kind.rows),
                 1, i, kind.petals]);
  }
  if (kind.disc > 0) pieces.push([new THREE.PlaneGeometry(1, 1), 2, 0, 1]);

  let nv = 0, ni = 0;
  for (const [geo] of pieces) {
    nv += geo.attributes.position.count;
    ni += geo.index.count;
  }
  const corner = new Float32Array(nv * 3);
  const normal = new Float32Array(nv * 3);
  const part = new Float32Array(nv * 3);
  const index = new Uint16Array(ni);
  let v = 0, i = 0;
  for (const [geo, id, idx, total] of pieces) {
    const p = geo.attributes.position.array;
    const nm = geo.attributes.normal.array;
    for (let k = 0; k < geo.index.count; k++) {
      index[i++] = geo.index.array[k] + v;
    }
    for (let k = 0; k < geo.attributes.position.count; k++) {
      corner.set([p[k * 3], p[k * 3 + 1], p[k * 3 + 2]], (v + k) * 3);
      normal.set([nm[k * 3], nm[k * 3 + 1], nm[k * 3 + 2]], (v + k) * 3);
      part.set([id, idx, total], (v + k) * 3);
    }
    v += geo.attributes.position.count;
  }

  const g = new THREE.InstancedBufferGeometry();
  g.setIndex(new THREE.BufferAttribute(index, 1));
  g.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array(nv * 3), 3));
  g.setAttribute('aCorner', new THREE.BufferAttribute(corner, 3));
  g.setAttribute('aPart', new THREE.BufferAttribute(part, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(normal, 3));
  g.instanceCount = count;
  return g;
}

/** One mesh, one draw call: every plant lives in the vertex shader. */
function plantMesh(field, kind, extent, petal, wind, sunDir,
                   shadows) {
  const geom = plantLattice(kind, field.n);
  const inst = (name, arr, size) => geom.setAttribute(
      name, new THREE.InstancedBufferAttribute(new Float32Array(arr), size));
  inst('iPos', field.pos, 3);
  inst('iShape', field.shape, 4);
  inst('iBend', field.bend, 4);
  inst('iVar', field.vary, 4);
  // position is zero, so the derived bounds would be a point at the
  // origin: state the box a fully bent patch really occupies.
  const half = extent / 2 + field.reach;
  geom.boundingBox = new THREE.Box3(
      new THREE.Vector3(-half, field.low, -half),
      new THREE.Vector3(half, field.high, half));
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(new THREE.Sphere());

  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.62, metalness: 0,
    side: THREE.DoubleSide, transparent: true, alphaTest: 0.08,
    name: 'FlowerPlant',
  });
  patchStandard(mat, {
    name: 'flowers:plant',
    uniforms: {
      uFlowWind: { value: wind.dir.clone() },
      uFlowSpeed: { value: wind.speed },
      uFlowBend: { value: _MAX_BEND },
      uFlowNod: { value: 0.22 },
      uFlowSpike: { value: kind.spike },
      uFlowStep: { value: kind.step },
      uFlowElev: { value: kind.elev },
      uFlowCurl: { value: kind.curl },
      uFlowCup: { value: kind.cup },
      uFlowSize: { value: new THREE.Vector2(kind.len, kind.wid) },
      uFlowDisc: { value: kind.disc },
      uFlowBulge: { value: kind.bulge },
      uFlowProf: { value: new THREE.Vector2(kind.prof[0], kind.prof[1]) },
      uFlowBlotch: { value: kind.blotch },
      uFlowPetal: { value: petal.clone() },
      uFlowPetalB: { value: siblingOf(petal, kind) },
      uFlowThroat: { value: new THREE.Color(kind.throat) },
      uFlowDiscCol: { value: new THREE.Color(kind.disc_) },
      uFlowStem: { value: new THREE.Color(0x4a6626) },
    },
    vertexHead: PLANT_VERTEX_HEAD,
    vertexBody: PLANT_VERTEX,
    fragmentHead: PLANT_FRAGMENT_HEAD,
    fragmentBody: PLANT_FRAGMENT,
  });
  // A petal is thin and lit through, and this is the thin-leaf patch:
  // one albedo lift toward the light behind it, no third copy of it.
  // The tint stays close to the PETAL: light through a red petal comes
  // out red, and a lift halfway to warm white desaturates a scarlet
  // poppy to salmon on exactly the plants the sun is behind.
  patchLeafSSS(mat, {
    sunDir: sunDir, strength: 0.42, power: 2.4,
    tint: petal.clone().lerp(new THREE.Color(0xfff2d0), 0.26),
  });
  // LAST in the chain, so it sees the transmitted lift as well as the
  // rim brightening. Both are multiplied onto an albedo that already
  // starts near paper on a white flower, and an albedo above 1 is not a
  // brighter petal — it is a petal with no shading left: the tone map
  // pins every pixel of it to the same white and the whole silhouette
  // goes to a paper cut-out. Measured on the daisy: 0.73 base * 1.4 rim
  // * 1.14 value + 0.20 transmitted = 1.36. The floor is the other end
  // of the same rule: a poppy's blotch is a deep bruise, not a hole.
  patchStandard(mat, {
    name: 'flowers:albedoRange',
    fragmentBody: '  diffuseColor.rgb = clamp(diffuseColor.rgb, '
        + 'vec3(0.015), vec3(0.86));',
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'Plants';
  mesh.receiveShadow = true;
  keepOutOfDepthPasses(mesh);
  // A meadow's worth of flower heads in the shadow map stamps a hard
  // black flower on the ground under each one, which reads as litter
  // rather than shade. Opt-in, as grass and wheat already are.
  if (!shadows) return mesh;
  // The AO guard must not cost the patch its shadow: the shadow pass
  // draws through its own hook with our depth material, so casting is
  // turned back on AFTER the guard, with the displacement it needs.
  shadowLike(mesh, 'flowers:plantDepth', PLANT_VERTEX_HEAD, PLANT_VERTEX);
  return cutDepthToSilhouette(mesh);
}

/**
 * Cut the shadow to the shape of the FLOWER, not of its cards.
 *
 * A petal is a rectangle that the fragment stage carves a petal out of,
 * and three's depth shader never sees that carving: measured on this
 * host, a shadowed daisy stamped thirteen hard slate parallelograms on
 * the ground and the patch read as litter dropped round the plants
 * rather than as shade. `patchStandard` writes a fragment body through
 * `<color_fragment>`, which the depth shader does not have — the whole
 * hook is silently dropped there — so the same cut is spliced in above
 * three's own alpha test, which is the one place a depth pass may
 * discard. The uniform map is the surface's own (`shadowLike` shares
 * it), so the profile the shadow is cut by is the profile that is drawn.
 */
function cutDepthToSilhouette(mesh) {
  const dep = mesh.customDepthMaterial;
  const compile = dep.onBeforeCompile;
  dep.onBeforeCompile = (shader, renderer) => {
    compile(shader, renderer);
    shader.fragmentShader = [
      'uniform vec2 uFlowProf;',
      'varying vec4 vFlow;',
      PLANT_PROFILE,
      shader.fragmentShader.replace(
          '#include <alphatest_fragment>',
          PLANT_DEPTH_CUT + '\n#include <alphatest_fragment>'),
    ].join('\n');
  };
  dep.needsUpdate = true;
  return mesh;
}

const PLANT_VERTEX_HEAD = [
  'uniform float uTime;',
  'uniform vec2 uFlowWind;',
  'uniform float uFlowSpeed;',
  'uniform float uFlowBend;',
  'uniform float uFlowNod;',
  'uniform float uFlowSpike;',
  'uniform float uFlowStep;',
  'uniform float uFlowElev;',
  'uniform float uFlowCurl;',
  'uniform float uFlowCup;',
  'uniform vec2 uFlowSize;',
  'uniform float uFlowDisc;',
  'uniform float uFlowBulge;',
  'attribute vec3 aCorner;',
  'attribute vec3 aPart;',
  'attribute vec3 iPos;',
  'attribute vec4 iShape;',
  'attribute vec4 iBend;',
  'attribute vec4 iVar;',
  'varying vec4 vFlow;',
  'varying vec3 vFlowVar;',
  // A world direction as the LOCAL offset that moves this surface one
  // metre along it, valid while the basis columns stay orthogonal.
  'vec3 flowLocalDir(vec3 w) {',
  '  mat3 m = mat3(modelMatrix);',
  '  return vec3(dot(w, m[0]) / max(dot(m[0], m[0]), 1e-6),',
  '              dot(w, m[1]) / max(dot(m[1], m[1]), 1e-6),',
  '              dot(w, m[2]) / max(dot(m[2], m[2]), 1e-6));',
  '}',
  // The gust field grass.js rides: streaks running downwind, so a
  // scene's meadow and its flowers are pushed by the same air.
  'float flowSway(vec3 root, float ph, float lag) {',
  '  vec2 w = uFlowWind;',
  '  float t = uTime * uFlowSpeed;',
  '  float s = astraStagger(ph + root.x * 0.07 + root.z * 0.11);',
  '  return (sin(t * 1.7 + s - lag)',
  '      + 0.35 * sin(t * 2.9 + s * 1.7 - lag)) / 1.35;',
  '}',
  'vec2 flowBend(vec3 root, vec4 b) {',
  '  vec2 w = uFlowWind;',
  '  vec2 q = vec2(dot(root.xz, w), dot(root.xz, vec2(-w.y, w.x)));',
  '  float t = uTime * uFlowSpeed;',
  '  float gust = astraFbm2(vec2(q.x * 0.055 - t * 0.5, q.y * 0.21), 2);',
  '  float push = (0.25 + 1.25 * gust)',
  '      * (0.62 + 0.38 * flowSway(root, b.w, 0.0));',
  '  return vec2(cos(b.y), sin(b.y)) * b.z + w * (b.x * push);',
  '}',
  // A circular arc of the stem's own length: a translated tip would
  // stretch the stem instead of laying it over.
  'void flowArc(float v, float h, float k, vec2 f, out vec3 p,',
  '             out vec3 tn) {',
  '  float a = k * v;',
  '  float u = h * (1.0 - cos(a)) / k;',
  '  p = vec3(f.x * u, h * sin(a) / k, f.y * u);',
  '  tn = vec3(f.x * sin(a), cos(a), f.y * sin(a));',
  '}',
].join('\n');

const PLANT_VERTEX = [
  '  vec3 flRoot = iPos;',
  '  vec2 flB = flowBend(flRoot, iBend);',
  '  float flL = length(flB);',
  '  vec2 flF = flL > 1e-5 ? flB / flL : vec2(1.0, 0.0);',
  '  float flK = clamp(flL, 1e-3, uFlowBend);',
  '  float flPart = aPart.x;',
  // Florets ride DOWN the stem on a spike and gather at the tip on a
  // disc or a cup: one uniform, three silhouettes.
  '  float flV = flPart < 0.5 ? aCorner.y',
  '      : 1.0 - uFlowSpike * (aPart.y / max(aPart.z - 1.0, 1.0));',
  '  vec3 flP, flT;',
  '  flowArc(flV, iShape.x, flK, flF, flP, flT);',
  '  vec3 flAt = flRoot + flP;',
  // hue lean, tone, and where this plant sits between the kind's two
  // petal albedos — hashed off the whorl phase so it is decorrelated
  // from both of the others and costs no attribute.
  '  vFlowVar = vec3(iVar.x, iVar.w, astraHash11(iVar.y * 1.37 + 0.21));',
  '  if (flPart < 0.5) {',
  '    vec3 flW = (modelMatrix * vec4(flAt, 1.0)).xyz;',
  '    vec3 flView = normalize(flowLocalDir(cameraPosition - flW));',
  '    vec3 flC = cross(flT, flView);',
  '    float flCl = length(flC);',
  '    vec3 flSide = flCl > 1e-4 ? flC / flCl : vec3(1.0, 0.0, 0.0);',
  '    float flX = aCorner.x * 2.0;',
  '    float flWid = iShape.y * (0.55 + 0.45 * sqrt(max(1.0 - flV, 0.0)));',
  '    transformed = flAt + flSide * (flX * flWid);',
  '    vFlow = vec4(0.0, flV, flX, 0.0);',
  '#ifndef FLAT_SHADED',
  // The normal of a round stalk, not of a card.
  '    vec3 flFace = normalize(cross(flSide, flT));',
  '    vNormal = normalize(normalMatrix * normalize(',
  '        flFace * sqrt(max(1.0 - flX * flX, 0.04)) + flSide * flX));',
  '#endif',
  '  } else {',
  // The head is heavy, so it nods a beat BEHIND the stem it sits on.
  '    vec3 flUp = normalize(flT + vec3(uFlowWind.x, 0.0, uFlowWind.y)',
  '        * (uFlowNod * flowSway(flRoot, iBend.w, 0.9)));',
  '    vec3 flRef = abs(flUp.z) < 0.9 ? vec3(0.0, 0.0, 1.0)',
  '        : vec3(1.0, 0.0, 0.0);',
  '    vec3 flR = normalize(cross(flUp, flRef));',
  '    vec3 flFwd = cross(flR, flUp);',
  '    float flJit = astraHash21(vec2(aPart.y, iVar.z));',
  '    float flAng = (aPart.y + 0.5) * uFlowStep + iVar.y',
  '        + (flJit - 0.5) * 0.45;',
  '    vec3 flDir = cos(flAng) * flR + sin(flAng) * flFwd;',
  '    vec3 flAx = normalize(cross(flUp, flDir));',
  '    float flS = iShape.z * (0.75 + 0.5 * flJit);',
  '    if (flPart > 1.5) {',
  '      float flD = uFlowDisc * iShape.z * 2.0;',
  '      float flQ = (aCorner.x * aCorner.x + aCorner.y * aCorner.y) * 4.0;',
  '      transformed = flAt + flR * (aCorner.x * flD)',
  '          + flFwd * (aCorner.y * flD)',
  '          + flUp * (uFlowBulge * iShape.z * max(1.0 - flQ, 0.0));',
  '      vFlow = vec4(2.0, aCorner.y, aCorner.x, 0.0);',
  '#ifndef FLAT_SHADED',
  // vNormal must match the WINDING, not the intent: three flips it by
  // gl_FrontFacing on a double-sided face, so a normal that disagrees
  // is turned away from the eye and the face renders unlit.
  '      vNormal = normalize(normalMatrix * flUp);',
  '#endif',
  '    } else {',
  // Lifted off the head plane by its own openness, curled along its
  // length and cupped across it: flat disc, deep cup, or floret.
  '      float flU = aCorner.y + 0.5;',
  '      float flEl = uFlowElev * iShape.w;',
  '      vec3 flOut = flDir * cos(flEl) + flUp * sin(flEl);',
  '      float flLen = uFlowSize.x * flS;',
  '      float flWid = uFlowSize.y * flS;',
  '      vec3 flDu = flOut * flLen',
  '          + flUp * (2.0 * uFlowCurl * flU * flLen);',
  '      vec3 flDs = flAx * flWid',
  '          + flUp * (2.0 * uFlowCup * aCorner.x * flWid);',
  // Petals start at the RIM of the boss, or thirteen of them bury the
  // one part of a daisy that says daisy.
  '      transformed = flAt + flOut * (uFlowDisc * iShape.z)',
  '          + flOut * (flU * flLen)',
  '          + flUp * (uFlowCurl * flU * flU * flLen)',
  '          + flAx * (aCorner.x * flWid)',
  '          + flUp * (uFlowCup * aCorner.x * aCorner.x * flWid);',
  '      vFlow = vec4(1.0, flU, aCorner.x * 2.0, flJit);',
  '#ifndef FLAT_SHADED',
  '      vNormal = normalize(normalMatrix * normalize(cross(flDu, flDs)));',
  '#endif',
  '    }',
  '  }',
].join('\n');

// Half-width of a petal at u along it. (p, q) is the whole outline:
// p toward 3 gives parallel sides with round ends, q under 0.5 a
// blunt tip, both together a broad fan. Shared with the depth pass,
// which cuts the shadow by the same curve.
const PLANT_PROFILE = [
  'float flowProfile(float u, vec2 pq) {',
  '  return pow(max(1.0 - pow(abs(2.0 * u - 1.0), pq.x), 0.0), pq.y);',
  '}',
].join('\n');

// The colour pass discards outside the petal and inside nothing else;
// this is that test with the shading taken out.
//
// What this CANNOT fix is a shadow texel wider than the flower: the
// default `sunRig` fits its frustum to a 150 m scene, which is 7 cm a
// texel at 4096, and a daisy head is 5 cm. At that ratio every kind of
// flower casts the same one-texel slate block whatever shape it really
// is, which is why `shadows` is opt-in and why the honest place to turn
// it on is a scene whose sun rig is fitted to the patch (`bounds: 12`
// puts six texels across a head, measured below).
const PLANT_DEPTH_CUT = [
  '  if (vFlow.x > 1.5) {',
  '    if (length(vec2(vFlow.y, vFlow.z)) * 2.0 > 1.0) discard;',
  '  } else if (vFlow.x > 0.5) {',
  '    if (abs(vFlow.z) > flowProfile(vFlow.y, uFlowProf)) discard;',
  '  }',
].join('\n');

const PLANT_FRAGMENT_HEAD = [
  'uniform vec2 uFlowProf;',
  'uniform float uFlowBlotch;',
  'uniform vec3 uFlowPetal;',
  'uniform vec3 uFlowPetalB;',
  'uniform vec3 uFlowThroat;',
  'uniform vec3 uFlowDiscCol;',
  'uniform vec3 uFlowStem;',
  'varying vec4 vFlow;',
  'varying vec3 vFlowVar;',
  PLANT_PROFILE,
].join('\n');

const PLANT_FRAGMENT = [
  '  vec3 flCol;',
  '  float flA = 1.0;',
  '  if (vFlow.x < 0.5) {',
  // A patch is DEEP: the foot of a stem sits in its neighbours' shade.
  '    flCol = uFlowStem * (0.42 + 0.58 * smoothstep(0.0, 0.5, vFlow.y));',
  '    flCol *= 0.93 + 0.14 * astraNoise2(vec2(vFlow.y * 40.0, 3.0));',
  // Green is the one hue a hue ROTATION cannot vary safely (it is the
  // channel that would go negative), and a saturated kind rotates
  // barely at all: this is the stem's own yellow-green to blue-green
  // swing, on the same per-plant number the petals mix by.
  '    flCol *= mix(vec3(1.10, 1.0, 0.84), vec3(0.88, 1.0, 1.10),',
  '        vFlowVar.z);',
  '  } else if (vFlow.x > 1.5) {',
  '    float flR = length(vec2(vFlow.y, vFlow.z)) * 2.0;',
  '    if (flR > 1.0) discard;',
  // A boss is packed with florets, and darker at its heart.
  '    flCol = uFlowDiscCol * (0.72 + 0.5 * astraNoise2(',
  '        vec2(vFlow.y, vFlow.z) * 90.0));',
  '    flCol *= 0.55 + 0.45 * smoothstep(0.15, 0.85, flR);',
  '  } else {',
  '    float flW = flowProfile(vFlow.y, uFlowProf);',
  '    float flD = abs(vFlow.z) / max(flW, 1e-3);',
  '    float flAa = max(fwidth(flD), 0.02);',
  '    float flM = 1.0 - smoothstep(1.0 - flAa, 1.0 + flAa, flD);',
  '    if (flM < 0.02) discard;',
  // The rim of a petal is one cell thick: it passes light rather than
  // reflecting it, so it goes both PALER and more transparent.
  '    float flCore = 1.0 - smoothstep(0.45, 1.0, flD);',
  '    flA = flM * (0.34 + 0.66 * flCore);',
  // This plant's own place between the kind's two petal albedos, and
  // THEN the throat: the gradient into the centre belongs to the
  // flower actually being drawn, not to the row in the table.
  '    vec3 flPet = mix(uFlowPetal, uFlowPetalB, vFlowVar.z);',
  '    flCol = mix(uFlowThroat, flPet, smoothstep(0.0, 0.45, vFlow.y));',
  '    flCol *= 1.0 + 0.25 * (1.0 - flCore);',
  // Veins run out from the base, and the throat of a poppy is a
  // blotch rather than a gradient — a deep bruise of the petal's own
  // colour, never a hole punched through it.
  '    flCol *= 0.94 + 0.13 * astraStroke(vFlow.z * 2.5, 0.22);',
  '    flCol = mix(flCol, max(uFlowThroat * 0.42, vec3(0.02)),',
  '        uFlowBlotch * (1.0 - smoothstep(0.02, 0.34, vFlow.y)));',
  // Petal to petal inside ONE head: a little tone, and a little warm
  // to cool with it, because a head is a cup whose near side is lit by
  // the sun and whose far side is lit by the sky.
  '    flCol *= 0.88 + 0.24 * vFlow.w;',
  '    flCol *= mix(vec3(1.035, 1.0, 0.955), vec3(0.965, 1.0, 1.045),',
  '        vFlow.w);',
  '  }',
  // Clamped at zero: astraHueShift is a rotation in RGB, and on a
  // saturated albedo it drives the small channels NEGATIVE. Left to run
  // on through the multiply, half a poppy field comes back magenta.
  '  flCol = max(astraHueShift(flCol, vFlowVar.x), vec3(0.0)) * vFlowVar.y;',
  '  diffuseColor.rgb = flCol;',
  '  diffuseColor.a *= flA;',
].join('\n');

/**
 * One row per falling kind: how it is shaped and how it comes down.
 * A leaf is longer, heavier and tumbles faster than a petal, which
 * hangs in the air and slides much further sideways per metre fallen.
 */
const _FALL_KINDS = {
  petal: {
    size: 0.030, aspect: 0.72, fall: 0.42, spin: 1.6, wob: 0.42,
    curl: 0.22, prof: [2.4, 0.52], notch: 0.55, vein: 0.06, hue: 0.15,
    color: 0xe8b3c0, sib: [-0.045, 0.10, -0.08],
  },
  leaf: {
    size: 0.075, aspect: 0.46, fall: 0.85, spin: 2.8, wob: 0.30,
    curl: 0.38, prof: [1.9, 0.55], notch: 0, vein: 0.22, hue: 0.22,
    color: 0xb0641c, sib: [-0.040, 0.05, -0.09],
  },
};

// Two octaves with a fixed envelope, so the slide is bounded by 1 and
// the stated volume is exactly the volume the field occupies. The GLSL
// below is these two functions, spelled the same.
function _wobX(f, p, t) {
  return Math.sin(f * t + p) * 0.75 + Math.sin(f * 1.9 * t + p * 2.1) * 0.25;
}

function _wobZ(f, p, t) {
  return Math.cos(f * t + p) * 0.75 + Math.cos(f * 2.3 * t + p * 1.6) * 0.25;
}

const _FALL_WOB_GLSL = [
  'float flowFallWobX(float f, float p, float t) {',
  '  return sin(f * t + p) * 0.75 + sin(f * 1.9 * t + p * 2.1) * 0.25;',
  '}',
  'float flowFallWobZ(float f, float p, float t) {',
  '  return cos(f * t + p) * 0.75 + cos(f * 2.3 * t + p * 1.6) * 0.25;',
  '}',
].join('\n');

/** Wrap v into [-lim, lim), the CPU mirror of GLSL mod(). */
function _wrap(v, lim) {
  return v - Math.floor((v + lim) / (2 * lim)) * 2 * lim;
}

/**
 * Leaves or petals drifting DOWN through the air.
 *
 * This is the cue that says autumn, or a cherry in bloom: the air
 * itself carrying something. A leaf does not fall straight, so no two
 * come down alike — each has its own fall rate, its own tumble about
 * its own axis (which turns it edge-on and back, the flicker that
 * reads as a leaf) and its own sideways slide, a curve that goes
 * nowhere in particular while the wind carries the whole field
 * downwind.
 *
 * The field WRAPS inside the volume it advertises — `extent` square,
 * `height` tall, its foot on y = 0 — so it never leaves the box it
 * states, and the box is what three culls it by.
 *
 * @param {object} [opts]
 *   `extent` metres square the field covers (default 8); `height`
 *   metres it stands (default 5); `count` pieces in the air (default
 *   240 — one draw call regardless); `kind` 'petal' (default, small,
 *   slow, notched at the tip, sliding far) or 'leaf' (longer, heavier,
 *   tumbling faster); `size` length in metres (default 0.030 petal /
 *   0.075 leaf); `color` albedo, not a screen colour, and spread either
 *   side of itself per piece the way the planted patch spreads its
 *   petals; `wind` grass.js's
 *   option in either spelling, so the drift matches the meadow's gusts;
 *   `seed` PRNG seed (default 13); `name` group name. The light coming
 *   THROUGH a piece is read from the scene's OWN lights, so there is no
 *   sun to state and nothing to keep in step.
 * @returns {THREE.Group} Named `Falling`, holding ONE instanced mesh
 *   `Drift`, with `userData.tick(t)` advancing the fall and
 *   `userData.sample(i, t)` giving piece i's group-local position — the
 *   CPU mirror of the vertex shader.
 */
export function makeFalling(opts = {}) {
  const kind = _FALL_KINDS[opts.kind] || _FALL_KINDS.petal;
  const extent = Math.max(0.5, opts.extent === undefined ? 8 : opts.extent);
  const height = Math.max(0.3, opts.height === undefined ? 5 : opts.height);
  const count = Math.max(1, Math.round(
      opts.count === undefined ? 240 : opts.count));
  const size = Math.max(0.002, opts.size === undefined ? kind.size : opts.size);
  const seed = opts.seed === undefined ? 13 : opts.seed;
  const wind = windOf(opts.wind);
  const color = new THREE.Color(
      opts.color === undefined ? kind.color : opts.color);
  // grass.js states wind as radians of tip bend; a metre per second of
  // downwind drift per 0.5 of it is a breeze that matches that sway.
  const drift = wind.dir.clone().multiplyScalar(2 * wind.amp);
  const wob = kind.wob * size / kind.size;
  const fall = kind.fall;

  const rand = mulberry32(seed);
  const pos = new Float32Array(count * 3);
  const fal = new Float32Array(count * 4);
  const axis = new Float32Array(count * 3);
  const swing = new Float32Array(count * 4);
  const vary = new Float32Array(count * 2);
  // Half the quad's own diagonal, plus the widest slide, is the room
  // the wrap has to leave so the field cannot cross its own box.
  const span = 0.5 * size * (1 + kind.aspect) * 1.35;
  const lim = Math.max(1e-3, extent / 2 - span - wob);
  const drop = Math.max(1e-3, height - 2 * span);
  for (let i = 0; i < count; i++) {
    pos[i * 3] = (rand() * 2 - 1) * lim;
    pos[i * 3 + 1] = rand() * drop;
    pos[i * 3 + 2] = (rand() * 2 - 1) * lim;
    // Its own fall rate, its own tumble: a field that comes down at
    // one speed reads as a sheet of confetti.
    fal[i * 4] = 0.62 + 0.76 * rand();
    fal[i * 4 + 1] = 0.75 + 0.55 * rand();
    fal[i * 4 + 2] = (rand() < 0.5 ? -1 : 1) * (0.5 + rand());
    fal[i * 4 + 3] = rand() * _TAU;
    const a = rand() * _TAU;
    const y = rand() * 1.4 - 0.7;
    const r = Math.sqrt(Math.max(1 - y * y, 0));
    axis[i * 3] = Math.cos(a) * r;
    axis[i * 3 + 1] = y;
    axis[i * 3 + 2] = Math.sin(a) * r;
    swing[i * 4] = 0.5 + 1.1 * rand();
    swing[i * 4 + 1] = rand() * _TAU;
    swing[i * 4 + 2] = 0.5 + 1.1 * rand();
    swing[i * 4 + 3] = rand() * _TAU;
    vary[i * 2] = (rand() - 0.5) * kind.hue;
    vary[i * 2 + 1] = 0.72 + 0.5 * rand();
  }

  // The REAL radius: `position` is all zeros, so this sphere is the
  // only thing three can cull the field by, and the box below is the
  // same volume stated the way the asset census reads it.
  const radius = 0.5 * Math.sqrt(2 * extent * extent + height * height);
  const geom = instancedQuad(count, 1, 1, radius);
  geom.setAttribute('aPos', new THREE.InstancedBufferAttribute(pos, 3));
  geom.setAttribute('aFall', new THREE.InstancedBufferAttribute(fal, 4));
  geom.setAttribute('aAxis', new THREE.InstancedBufferAttribute(axis, 3));
  geom.setAttribute('aWob', new THREE.InstancedBufferAttribute(swing, 4));
  geom.setAttribute('aVar', new THREE.InstancedBufferAttribute(vary, 2));
  // instancedQuad ships no `normal`, and a zero one NaNs through the
  // shadow-bias path of every lit material; the shader overwrites it.
  geom.setAttribute('normal', new THREE.PlaneGeometry(1, 1).attributes.normal);
  geom.boundingBox = new THREE.Box3(
      new THREE.Vector3(-extent / 2, 0, -extent / 2),
      new THREE.Vector3(extent / 2, height, extent / 2));
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(new THREE.Sphere());

  const mesh = new THREE.Mesh(geom, fallMaterial({
    kind, size, color, drift, wob, fall, lim, drop, span,
  }));
  mesh.name = 'Drift';
  mesh.renderOrder = 3;
  const g = new THREE.Group();
  g.name = opts.name || 'Falling';
  g.add(keepOutOfDepthPasses(mesh));
  g.userData.tick = (t) => tickShaders(g, t);
  g.userData.sample = (i, t) => {
    const k = Math.min(count - 1, Math.max(0, i | 0));
    const y = (pos[k * 3 + 1] - fall * fal[k * 4] * t) % drop;
    return new THREE.Vector3(
        _wrap(pos[k * 3] + drift.x * t, lim)
            + wob * _wobX(swing[k * 4], swing[k * 4 + 1], t),
        (y < 0 ? y + drop : y) + span,
        _wrap(pos[k * 3 + 2] + drift.y * t, lim)
            + wob * _wobZ(swing[k * 4 + 2], swing[k * 4 + 3], t));
  };
  return g;
}

/** Fall, tumble, slide and silhouette — all of it in GLSL. */
function fallMaterial(cfg) {
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.68, metalness: 0,
    side: THREE.DoubleSide, transparent: true, alphaTest: 0.08,
    name: 'FallingLeaf',
  });
  // FIRST, so this patch's body runs after it and can hand it the
  // world normal: a piece in the air takes every attitude, and the
  // half turned away from the sun is exactly the half lit THROUGH.
  // A piece in the air is nearly always seen against the SKY, and a
  // thin backlit petal against a bright sky is the one thing that has
  // to glow or it reads as a speck of dirt on the lens — measured
  // here: at 0.9/3 the drift over the horizon came back tan-brown at
  // half the sky's luminance. A wider lobe (lower power) is what puts
  // the light back into the pieces that are only half turned away.
  patchTranslucency(mat, {
    thickness: 0.0004, strength: 1.35, power: 2.2,
    color: cfg.color.clone().lerp(new THREE.Color(0xfff2d0), 0.5),
  });
  patchStandard(mat, {
    name: 'flowers:fall',
    uniforms: {
      uFallDrift: { value: cfg.drift.clone() },
      uFallRate: { value: cfg.fall },
      uFallSize: { value: new THREE.Vector2(
          cfg.size * cfg.kind.aspect, cfg.size) },
      uFallSpin: { value: cfg.kind.spin },
      uFallCurl: { value: cfg.kind.curl },
      uFallWob: { value: cfg.wob },
      uFallLim: { value: cfg.lim },
      uFallDrop: { value: cfg.drop },
      uFallFoot: { value: cfg.span },
      uFallProf: { value: new THREE.Vector2(
          cfg.kind.prof[0], cfg.kind.prof[1]) },
      uFallNotch: { value: cfg.kind.notch },
      uFallVein: { value: cfg.kind.vein },
      uFallColor: { value: cfg.color.clone() },
      uFallColorB: { value: siblingOf(cfg.color, cfg.kind) },
    },
    vertexHead: FALL_VERTEX_HEAD,
    vertexBody: FALL_VERTEX,
    fragmentHead: FALL_FRAGMENT_HEAD,
    fragmentBody: FALL_FRAGMENT,
  });
  return mat;
}

const FALL_VERTEX_HEAD = [
  'uniform float uTime;',
  'uniform vec2 uFallDrift;',
  'uniform float uFallRate;',
  'uniform vec2 uFallSize;',
  'uniform float uFallSpin;',
  'uniform float uFallCurl;',
  'uniform float uFallWob;',
  'uniform float uFallLim;',
  'uniform float uFallDrop;',
  'uniform float uFallFoot;',
  'attribute vec3 aCorner;',
  'attribute vec3 aPos;',
  'attribute vec4 aFall;',
  'attribute vec3 aAxis;',
  'attribute vec4 aWob;',
  'attribute vec2 aVar;',
  'varying vec4 vFall;',
  'varying vec3 vFallVar;',
  // finish.js's shared world pair. Its own vertex body reads the
  // `normal` ATTRIBUTE, which for geometry built in the vertex shader
  // is a constant — so this patch runs last and supplies the truth.
  'varying vec3 vAstraWorld;',
  'varying vec3 vAstraWorldN;',
  _FALL_WOB_GLSL,
].join('\n');

const FALL_VERTEX = [
  '  vec2 flW = vec2(flowFallWobX(aWob.x, aWob.y, uTime),',
  '      flowFallWobZ(aWob.z, aWob.w, uTime));',
  // Every piece wraps inside the stated box: its own fall rate down,
  // the shared wind across, its own slide on top of that.
  '  float flY = mod(aPos.y - uFallRate * aFall.x * uTime, uFallDrop);',
  '  vec2 flXZ = mod(aPos.xz + uFallDrift * uTime + uFallLim,',
  '      2.0 * uFallLim) - uFallLim + uFallWob * flW;',
  '  vec3 flP = vec3(flXZ.x, flY + uFallFoot, flXZ.y);',
  // The tumble axis lies IN the leaf, so the face turns edge-on and
  // back once a turn — that flicker is what reads as falling.
  '  vec3 flA = normalize(aAxis + 0.3 * vec3(flW.x, 0.0, flW.y));',
  '  vec3 flRef = abs(flA.y) < 0.9 ? vec3(0.0, 1.0, 0.0)',
  '      : vec3(1.0, 0.0, 0.0);',
  '  vec3 flE1 = normalize(cross(flA, flRef));',
  '  vec3 flE2 = cross(flA, flE1);',
  '  float flAng = uFallSpin * aFall.z * uTime + aFall.w;',
  '  vec3 flX = flE1 * cos(flAng) + flE2 * sin(flAng);',
  '  vec2 flS = uFallSize * aFall.y;',
  // A dry leaf is CURVED across its width, so its shading is a
  // gradient: flat cards turned from the sun read as black confetti.
  '  vec3 flN = cross(flA, flX);',
  '  transformed = flP + flX * (aCorner.x * flS.x)',
  '      + flA * (aCorner.y * flS.y)',
  '      + flN * (uFallCurl * aCorner.x * aCorner.x * flS.x);',
  '  vFall = vec4(uv, aFall.y, 0.0);',
  // hue lean, tone, and where this piece sits between the kind's two
  // albedos — hashed off its own slide phase, so no attribute grows.
  '  vFallVar = vec3(aVar, astraHash11(aWob.y * 1.37 + 0.21));',
  '  vec3 flNc = normalize(flN - 2.0 * uFallCurl * aCorner.x * flX);',
  '  vAstraWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;',
  '  vAstraWorldN = normalize((modelMatrix * vec4(flNc, 0.0)).xyz);',
  '#ifndef FLAT_SHADED',
  // The quad's own winding, y cross x: three flips this by
  // gl_FrontFacing, so the opposite sign leaves half the field unlit.
  '  vNormal = normalize(normalMatrix * flNc);',
  '#endif',
].join('\n');

const FALL_FRAGMENT_HEAD = [
  'uniform vec2 uFallProf;',
  'uniform float uFallNotch;',
  'uniform float uFallVein;',
  'uniform vec3 uFallColor;',
  'uniform vec3 uFallColorB;',
  'varying vec4 vFall;',
  'varying vec3 vFallVar;',
  'float flowFallProfile(float u, vec2 pq) {',
  '  return pow(max(1.0 - pow(abs(2.0 * u - 1.0), pq.x), 0.0), pq.y);',
  '}',
].join('\n');

const FALL_FRAGMENT = [
  '  float flU = vFall.y;',
  '  float flX = vFall.x * 2.0 - 1.0;',
  '  float flW = flowFallProfile(flU, uFallProf);',
  // A cherry petal is notched at its tip; a leaf runs to a point.
  '  flW -= uFallNotch * flW',
  '      * (1.0 - smoothstep(0.0, 0.30, length(vec2(flX, flU - 1.0))));',
  '  float flD = abs(flX) / max(flW, 1e-3);',
  '  float flAa = max(fwidth(flD), 0.03);',
  '  float flM = 1.0 - smoothstep(1.0 - flAa, 1.0 + flAa, flD);',
  '  if (flM < 0.02) discard;',
  // Thin at the rim: it passes light instead of reflecting it, so the
  // edge goes paler AND more transparent.
  '  float flCore = 1.0 - smoothstep(0.4, 1.0, flD);',
  // Its own place between the kind's two albedos first, then a SMALL
  // rotation clamped at zero: on a rust leaf a wide rotation drives
  // blue negative one way and green the other, and the field forks into
  // olive and pink instead of running through the browns between them.
  '  vec3 flCol = mix(uFallColor, uFallColorB, vFallVar.z);',
  '  flCol = max(astraHueShift(flCol, vFallVar.x), vec3(0.0)) * vFallVar.y;',
  '  flCol *= 1.0 + 0.45 * (1.0 - flCore);',
  '  flCol *= 1.0 - uFallVein * (1.0 - smoothstep(0.0, 0.09, abs(flX)))',
  '      * smoothstep(0.02, 0.2, flU);',
  '  flCol *= 0.93 + 0.12 * astraStroke(flU * 6.0, 0.24);',
  // The rim brightening and the tone jitter both multiply, and an
  // albedo over 1 is a piece with no shading left on it: the tone map
  // pins every pixel of it to the same white.
  '  diffuseColor.rgb = min(flCol, vec3(0.86));',
  '  diffuseColor.a *= flM * (0.42 + 0.58 * flCore);',
].join('\n');
