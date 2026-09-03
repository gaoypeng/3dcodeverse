/**
 * Grass: a field that reads as a field, from a low camera and from
 * above.
 *
 * Ground here is bare coloured geometry, and the two ways a first
 * attempt fails are opposite. From eye height a field of camera-facing
 * quads reads as coloured shards, so a blade is a real tapered strip
 * with WIDTH, a natural lean and a circular-arc bend — it curves, it
 * does not hinge, and it never stretches. From above, upright blades
 * present only their tips and the field reads as a scatter of dots, so
 * the blades ride on a `Sward` mat that carries the SAME clump field
 * they were placed by: the field is continuous where they are dense and
 * hands over to dry ground where they thin out.
 *
 * The blades are ONE draw call — one instanced strip whose every copy
 * is built in the vertex shader from per-blade attributes. `position`
 * stays at zero for the GTAO reason `instancedQuad` documents.
 *
 * Not `patchWind` from foliage_shade.js: that bends a plant from its
 * INSTANCE ORIGIN (`instanceMatrix`, one phase per plant) and moves the
 * tip by a straight translation. These blades have no instanceMatrix —
 * they are attributes on one geometry, so every blade would take the
 * world origin's phase and the whole field would nod in step — and a
 * translated tip stretches a blade that must instead lie down along an
 * arc of its own fixed length. Hence its own vertex path.
 */

import * as THREE from 'three';
import { fbm2, mulberry32 } from './noise.js';
import { patchStandard, shadowLike, tickShaders } from './shader.js';

// Blade shape: enough rows to curve, one column because the width is
// swept in the shader. 4 rows is where a bent blade stops faceting.
const BLADE_ROWS = 4;

// A world direction as the LOCAL offset that moves this surface one
// metre along it — the same projection foliage_shade.js uses, valid
// because compose() leaves the basis columns orthogonal.
const LOCAL_DIR = [
  'vec3 grassLocalDir(vec3 w) {',
  '  mat3 m = mat3(modelMatrix);',
  '  return vec3(dot(w, m[0]) / max(dot(m[0], m[0]), 1e-6),',
  '              dot(w, m[1]) / max(dot(m[1], m[1]), 1e-6),',
  '              dot(w, m[2]) / max(dot(m[2], m[2]), 1e-6));',
  '}',
].join('\n');

/**
 * Cover a patch of ground in grass.
 *
 * The whole field is deterministic in `seed`: same seed, same blades.
 * Drive it from your `tick()` — `grass.userData.tick(t)` — or the wind
 * never blows.
 *
 * @param {object} [opts]
 *   `extent` metres of the square patch's side (default 24, centred on
 *   the group's origin); `density` blades per square metre (default
 *   180; `maxBlades` caps it by widening the spacing, never by
 *   dropping a corner); `height` blade height in metres (default 0.42,
 *   taller in the clumps); `color` the lush green of a clump;
 *   `dryColor` the straw of the thin ground between them; `wind`
 *   strength multiplier, or `{dir, strength, speed}` with `dir` a
 *   THREE.Vector2 / [x, z] the direction it BLOWS TOWARD over world XZ
 *   (default a light breeze toward +X, strength 1, speed 1 = a
 *   two-second sway); `heightAt` (x, z) => y, the caller's ground
 *   height — blades and mat both follow it; `seed` PRNG seed (default
 *   11); `patchy` 0..1 how bare the ground goes between clumps (default
 *   0.5); `maxBlades` hard cap (default 60000); `sward` draw the ground
 *   mat (default true — pass false when the ground already carries a
 *   grass splat); `shadows` cast blade shadows through a displaced
 *   depth pass (default false — tens of thousands of instances in the
 *   shadow map are a real cost on SwiftShader; wheat set the same
 *   default); `name` group name.
 * @returns {THREE.Group} Named `Grass`, resting on y = 0 (or on
 *   `heightAt`), holding `Blades` (one draw call) and `Sward`, with
 *   `userData.tick(t)` driving both.
 */
export function makeGrass(opts = {}) {
  const extent = opts.extent === undefined ? 24 : opts.extent;
  const density = opts.density === undefined ? 180 : opts.density;
  const height = opts.height === undefined ? 0.42 : opts.height;
  const seed = opts.seed === undefined ? 11 : opts.seed;
  const patchy = clamp01(opts.patchy === undefined ? 0.5 : opts.patchy);
  const maxBlades = opts.maxBlades === undefined ? 60000 : opts.maxBlades;
  // Albedos, not screen colours: a scene sun runs at 5-6, so a hex
  // that already looks like sunlit grass tone-maps to pale felt.
  // CHROMA, though, has to be spent up front. The sky fill is blue at
  // 1.2-1.4 and the env is a blue-white dome, so every green here is
  // washed toward mint on the way to the frame: measured on this
  // renderer, the old 0x44631f field landed at saturation 0.28 with a
  // mean luminance of 0.56 — pale felt by a different route.
  const lush = new THREE.Color(
      opts.color === undefined ? 0x375e18 : opts.color);
  const dry = new THREE.Color(
      opts.dryColor === undefined ? 0x706333 : opts.dryColor);
  const wind = windOf(opts.wind);
  const ground = typeof opts.heightAt === 'function' ? opts.heightAt : null;
  const clumpAt = clumpField(seed, extent);

  const g = new THREE.Group();
  g.name = opts.name || 'Grass';
  const field = plantField(
      extent, density, maxBlades, height, patchy, seed, clumpAt, ground);
  g.add(bladeMesh(field, extent, lush, dry, wind, opts.shadows === true));
  if (opts.sward !== false) {
    g.add(swardMat(extent, density, lush, dry, clumpAt, ground));
  }
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

/** Clamp to 0..1 without importing MathUtils for two calls. */
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

/**
 * Read the `wind` option in either of its two spellings.
 *
 * Exported because one scene's wind has to move its meadow,
 * its reeds and its trees as ONE wind: a library that
 * re-implements this reader is a library that will drift
 * away from it.
 */
export function windOf(w) {
  const o = (w && typeof w === 'object') ? w : { strength: w };
  const d = o.dir;
  const dir = new THREE.Vector2(
      d ? (d.x === undefined ? d[0] : d.x) : 1,
      d ? (d.y === undefined ? d[1] : d.y) : 0.45);
  if (dir.lengthSq() < 1e-9) dir.set(1, 0);
  return {
    dir: dir.normalize(),
    // Radians of tip bend at a full gust, from a 1 = breeze strength.
    amp: 0.5 * (o.strength === undefined ? 1 : o.strength),
    speed: o.speed === undefined ? 1 : o.speed,
  };
}

/**
 * The tussock field, on the CPU so blades and mat agree on it.
 *
 * GLSL_UTIL's noise is NOT noise.js's, so a shader-side clump field
 * would put the mat's green somewhere else entirely; this one value
 * feeds the placement, the height, the colour and the mat's vertices.
 */
function clumpField(seed, extent) {
  const o = { seed, octaves: 4, gain: 0.62 };
  const raw = (x, z) => fbm2(x * 0.38, z * 0.38, o);
  // Measured: fbm2 spans about +-0.25 here, off-centre by as much as
  // half of that, and by a different amount per seed and per region.
  // Uncalibrated, `patchy` would mean a different field every time.
  const n = 24;
  let sum = 0, sq = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = raw(extent * ((i + 0.5) / n - 0.5),
                    extent * ((j + 0.5) / n - 0.5));
      sum += v;
      sq += v * v;
    }
  }
  const mean = sum / (n * n);
  const sd = Math.sqrt(Math.max(sq / (n * n) - mean * mean, 1e-9));
  return (x, z) => {
    // Contrast, or every patch lands mid-field and the whole extent is
    // one wash: a tussock is either there or it is not.
    const v = clamp01(0.5 + (raw(x, z) - mean) / (2.6 * sd));
    return 0.45 * v + 0.55 * v * v * (3 - 2 * v);
  };
}

/**
 * Place the blades on a fully jittered grid, thinned by the clumps.
 *
 * Full-cell jitter, not scatterGrid's 0.35: from directly above, a
 * partly jittered lattice still shows its rows, which is the "grid of
 * dots" this library exists to avoid.
 */
function plantField(extent, density, maxBlades, height, patchy, seed,
                    clumpAt, ground) {
  const area = extent * extent;
  const want = Math.max(1, Math.min(Math.round(density * area), maxBlades));
  // Over-provisioned for the clump rejection, so `density` survives
  // `patchy`; and a maxBlades cap widens the CELL instead of dropping a
  // corner, which makes a capped field thinner and not smaller.
  const cells = Math.max(1, Math.round(Math.sqrt(want / (1 - patchy * 0.4))));
  const step = extent / cells;
  const rand = mulberry32(seed);
  const pos = [], shape = [], vary = [];
  let n = 0, maxH = 0;
  for (let iz = 0; iz < cells && n < maxBlades; iz++) {
    for (let ix = 0; ix < cells && n < maxBlades; ix++) {
      const x = -extent / 2 + (ix + rand()) * step;
      const z = -extent / 2 + (iz + rand()) * step;
      const c = clumpAt(x, z);
      if (rand() > 1 - patchy * (1 - c)) continue;
      // A few seed stalks stand well clear of the sward: without them
      // the field's top edge is a ruled line from any low camera.
      const tall = rand() < 0.06 * c ? 1.5 + 0.7 * rand() : 1;
      const h = height * tall * (0.55 + 0.75 * c) * (0.8 + 0.45 * rand());
      pos.push(x, ground ? ground(x, z) : 0, z);
      // A blade droops in its own direction; the wind adds to that
      // vector in the shader, so a still field leans every which way.
      shape.push(h, h * (0.042 + 0.030 * rand()),
                 0.16 + 0.42 * rand() * (1.2 - c), rand() * Math.PI * 2);
      // Dryness follows the CLUMP with only a little blade-to-blade
      // jitter: more, and the patches dissolve into per-blade noise.
      // Hue per blade, and it has to be WIDE: a meadow is yellow-green
      // to blue-green to dried straw, not one green shaded by light.
      // Measured, +-6 deg read as a single albedo at any distance
      // (local hue spread 8 deg against 25-45 in a photograph).
      vary.push((rand() - 0.5) * 1.3, rand(),
                clamp01(0.75 * (1 - c) + (rand() - 0.5) * 0.18),
                (rand() - 0.5) * 0.85);
      maxH = Math.max(maxH, h);
      n++;
    }
  }
  return { n, pos, shape, vary, maxH };
}

/**
 * The blade lattice: ONE instanced strip with `position` pinned at 0.
 *
 * GTAOPass redraws with an override material that ignores this vertex
 * shader, so a real blade left in `position` would stack every copy at
 * the world origin and burn a black slab there. The strip rides
 * `aCorner` (x across, y from root to tip) as `instancedQuad` does.
 */
function bladeLattice(count) {
  const base = new THREE.PlaneGeometry(1, 1, 1, BLADE_ROWS);
  base.translate(0, 0.5, 0);
  const g = new THREE.InstancedBufferGeometry();
  g.index = base.index;
  g.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array(base.attributes.position.count * 3), 3));
  g.setAttribute('aCorner', base.attributes.position);
  g.setAttribute('normal', base.attributes.normal);
  g.setAttribute('uv', base.attributes.uv);
  g.instanceCount = count;
  return g;
}

/** One mesh, one draw call: every blade lives in the vertex shader. */
function bladeMesh(field, extent, lush, dry, wind, shadows) {
  const geom = bladeLattice(field.n);
  const inst = (name, arr, size) => geom.setAttribute(
      name, new THREE.InstancedBufferAttribute(new Float32Array(arr), size));
  inst('iPos', field.pos, 3);
  inst('iShape', field.shape, 4);
  inst('iVar', field.vary, 4);
  // position is zero, so the derived bounds would be a point at the
  // origin: state the field's real box, and never cull on it.
  let low = 0, high = 0;
  for (let i = 1; i < field.pos.length; i += 3) {
    low = Math.min(low, field.pos[i]);
    high = Math.max(high, field.pos[i]);
  }
  geom.boundingBox = new THREE.Box3(
      new THREE.Vector3(-extent / 2, low, -extent / 2),
      new THREE.Vector3(extent / 2, high + field.maxH, extent / 2));
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(
      new THREE.Sphere());

  // PHYSICAL, not Standard, for one uniform: `specularIntensity`.
  // Measured on this renderer — blade albedo forced to BLACK and the
  // environment off — the field still came back at luminance 0.36,
  // saturation 0.07: a flat neutral sheen over every blade, and 70% of
  // what each pixel was made of. That is the dielectric's own
  // reflection, and it is the whole reason no albedo could make this
  // grass read as green. A Standard dielectric's F90 is pinned at 1.0,
  // so a field seen nearly edge-on from a low camera reflects the sky
  // at full strength everywhere; only `specularIntensity` (which
  // scales F0 AND F90) can turn it down, and a blade of grass is a
  // waxy leaf, not polished glass. The cost is the physical fragment
  // path for one material and one draw call.
  const mat = new THREE.MeshPhysicalMaterial({
    color: 0xffffff, roughness: 0.78, metalness: 0,
    specularIntensity: 0.30,
    side: THREE.DoubleSide, name: 'GrassBlade',
    // And the AO this host does not have: the reference ran GTAO over
    // the field, we have no post chain, and a blade standing in a
    // sward sees a slot of sky rather than a dome.
    envMapIntensity: 0.62,
  });
  patchStandard(mat, {
    name: 'grass:blade',
    uniforms: {
      uGrassLush: { value: lush.clone() },
      uGrassDry: { value: dry.clone() },
      uGrassWind: { value: wind.dir.clone() },
      uGrassAmp: { value: wind.amp },
      uGrassSpeed: { value: wind.speed },
    },
    vertexHead: BLADE_HEAD,
    vertexBody: BLADE_VERTEX,
    fragmentHead: [
      'uniform vec3 uGrassLush;',
      'uniform vec3 uGrassDry;',
      'varying vec3 vGrass;',
      'varying vec2 vGrassW;',
    ].join('\n'),
    fragmentBody: BLADE_FRAGMENT,
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'Blades';
  mesh.frustumCulled = false;
  mesh.receiveShadow = true;
  // Without the displaced depth pass a shadow pass draws only the
  // degenerate zero-position quads, so casting stays off by default.
  mesh.castShadow = false;
  if (shadows) {
    shadowLike(mesh, 'grass:bladeDepth', BLADE_HEAD, BLADE_VERTEX);
  }
  return mesh;
}

const BLADE_HEAD = [
  'uniform float uTime;',
  'uniform vec2 uGrassWind;',
  'uniform float uGrassAmp;',
  'uniform float uGrassSpeed;',
  'attribute vec3 aCorner;',
  'attribute vec3 iPos;',
  'attribute vec4 iShape;',
  'attribute vec4 iVar;',
  'varying vec3 vGrass;',
  'varying vec2 vGrassW;',
  LOCAL_DIR,
].join('\n');

const BLADE_VERTEX = [
  '  vec3 grBase = iPos;',
  '  float grV = aCorner.y;',
  // Gusts are streaks running downwind, so the field is compressed
  // ALONG the wind and travels with it; across it, structure is fine.
  '  vec2 grW = uGrassWind;',
  '  vec2 grP = vec2(dot(grBase.xz, grW),',
  '                  dot(grBase.xz, vec2(-grW.y, grW.x)));',
  '  float grGust = astraFbm2(',
  '      vec2(grP.x * 0.055 - uTime * uGrassSpeed * 0.5, grP.y * 0.21), 2);',
  // Neighbours out of phase: offsets under a full turn leave a whole
  // row nodding in step.
  '  float grPh = astraStagger(iVar.y + grBase.x * 0.07',
  '      + grBase.z * 0.11);',
  '  float grT = uTime * uGrassSpeed;',
  '  float grS = (sin(grT * 2.1 + grPh)',
  '      + 0.35 * sin(grT * 3.7 + grPh * 1.7)) / 1.35;',
  '  float grPush = uGrassAmp * (0.25 + 1.25 * grGust)',
  '      * (0.62 + 0.38 * grS);',
  // Droop and push ADD as vectors: still, the field leans every which
  // way; in a gust the whole of it turns downwind together.
  '  vec2 grLean = vec2(cos(iShape.w), sin(iShape.w)) * iShape.z;',
  '  vec2 grBend = grLean + grW * grPush;',
  '  float grA = min(length(grBend), 1.35);',
  '  vec2 grF = grA > 1e-4 ? normalize(grBend) : vec2(1.0, 0.0);',
  // A circular arc of the blade's own length: a translated tip would
  // stretch the blade instead of laying it down.
  '  float grK = max(grA, 1e-3);',
  '  float grH = iShape.x;',
  '  float grSin = sin(grK * grV), grCos = cos(grK * grV);',
  '  vec3 grSpine = grBase + vec3(grF.x * grH * (1.0 - grCos) / grK,',
  '      grH * grSin / grK, grF.y * grH * (1.0 - grCos) / grK);',
  '  vec3 grTan = vec3(grF.x * grSin, grCos, grF.y * grSin);',
  '  vec3 grPerp = vec3(-grF.y, 0.0, grF.x);',
  '  vec3 grUp = normalize(cross(grTan, grPerp));',
  // Twist: without it every blade in a gust turns its edge to the same
  // side and the field goes to slivers all at once.
  '  vec3 grSide = normalize(grPerp * cos(iVar.x) + grUp * sin(iVar.x));',
  '  vec3 grWpos = (modelMatrix * vec4(grSpine, 1.0)).xyz;',
  '  vec3 grView = normalize(grassLocalDir(cameraPosition - grWpos));',
  '  vec3 grCross = cross(grTan, grView);',
  '  float grCl = length(grCross);',
  // Edge-on, a strip IS a line. Turn it partway toward the eye and
  // widen what is left, or a low camera sees a scatter of shards.
  '  vec3 grFace = grCl > 1e-3 ? grCross / grCl : grSide;',
  '  float grEdge = abs(dot(grSide, grView));',
  '  grSide = normalize(mix(grSide,',
  '      grFace * (dot(grFace, grSide) < 0.0 ? -1.0 : 1.0),',
  '      0.5 * grEdge * grEdge));',
  '  float grWid = iShape.y * sqrt(max(1.0 - grV * grV, 0.0))',
  '      * (1.0 + 0.9 * grEdge * grEdge);',
  '  transformed = grSpine + grSide * (aCorner.x * grWid);',
  // The blade's ROOT in world XZ, not its bending spine: the mat under
  // it reads the same coordinate, so a straw patch is straw in both,
  // and a value that moved with the gust would make the field's colour
  // slide about under the wind.
  '  vGrassW = (modelMatrix * vec4(grBase, 1.0)).xz;',
  // Dryness swings at the METRE as well as per blade. The clump field
  // already dries the thin ground, but a meadow also goes to straw in
  // bands that have nothing to do with how dense it is — sun, drainage,
  // where it was cut — and those bands are what a still frame reads.
  '  float grDry = clamp(iVar.z',
  '      + (astraFbm2(vGrassW * 0.35 + 5.0, 2) - 0.375) * 0.7, 0.0, 1.0);',
  '  vGrass = vec3(grV, grDry, iVar.w);',
  '#ifndef FLAT_SHADED',
  // Cupped across its width, so a blade shades as a channel and not as
  // a flat card — and wound the way the STRIP is, which is the fix for
  // the worst bug in this file.
  //
  // The sweep sends the plane's x to grSide and its y to grTan, so the
  // face three rasterises as the front one has the normal
  // cross(grSide, grTan). This wrote cross(grTan, grSide) — the other
  // one. DOUBLE_SIDED cannot rescue it: three multiplies vNormal by
  // gl_FrontFacing, and gl_FrontFacing is decided by the same winding
  // vNormal was built from, so both flip together and the product is
  // invariant. Every blade shaded by its BACK face, always.
  //
  // Backlit that reads as merely soft. Front-lit it is a black field:
  // measured on our night rig, the whole meadow came back at luminance
  // 0.009 with a lit rock and lit ground beside it, and forcing a flat
  // 0.18 albedo proved the moon was reaching none of it. With the
  // winding right, the same frame lands at 0.049 and the frame's dark
  // fraction falls from 0.42 to 0.19.
  '  vNormal = normalize(normalMatrix * normalize(',
  '      cross(grSide, grTan) - grSide * aCorner.x * 1.2));',
  '#endif',
].join('\n');

const BLADE_FRAGMENT = [
  '  vec3 grC = mix(uGrassLush, uGrassDry, vGrass.y);',
  '  grC = astraHueShift(grC, vGrass.z);',
  // Broken colour at the METRE, on top of the per-blade jitter. One
  // blade is a couple of pixels wide, so its own hue averages straight
  // back out at any distance — measured, quadrupling the per-blade
  // swing moved the frame by under two degrees. Warm where the sun
  // lands, cool where only the sky reaches, over half a metre, is what
  // survives to the frame, and the mat reads the same field.
  '  grC = astraHueBreak(grC, vGrassW, 0.55, 0.62);',
  // The sward is DEEP: a blade's base sits in the shade of its
  // neighbours, and that gradient is most of what reads as grass.
  // Deeper than it looks, too — this host has no AO pass, so the
  // occlusion the reference got from GTAO has to live here or the
  // field has no dark interior at all (measured: p05 0.33 over the
  // field, against 0.08-0.15 in a photograph of one).
  // A POWER, not a smoothstep: a smoothstep that reaches the root is
  // already back at 0.9 by mid-blade, which is where most of the
  // field's pixels are, so the frame keeps none of it. Light falls
  // through a sward roughly as a power of the height, and 1.7 is what
  // put this field's fifth percentile in a photograph's range.
  '  float grDepth = pow(clamp(vGrass.x, 0.0, 1.0), 1.7);',
  '  grC *= 0.22 + 0.78 * grDepth;',
  '  grC += uGrassLush * 0.10 * smoothstep(0.68, 1.0, vGrass.x);',
  // TRANSLUCENCY. A blade is a fraction of a millimetre of sap: with
  // the sun behind it most of what reaches the eye came THROUGH it,
  // and that gold is the difference between a meadow and a green
  // carpet. Albedo cannot say it — the lit side of this fragment is
  // the side facing AWAY — so it goes in as emitted radiance, read off
  // the scene's own key light in view space (direction AND colour, so
  // it cannot disagree with the sun, and a night rig's moon drives it
  // just as well). Sap is yellower than the leaf, and a negative
  // rotation about the grey axis takes a green toward yellow.
  '#if NUM_DIR_LIGHTS > 0',
  '#ifndef FLAT_SHADED',
  '  vec3 grN = normalize(vNormal);',
  '  vec3 grEye = normalize(vViewPosition);',
  '  vec3 grL = directionalLights[0].direction;',
  // Both tests, or a front-lit field glows as brightly as a backlit
  // one: the light has to be on the far side of the blade FROM THE
  // EYE, and the eye has to be looking back down the beam.
  '  float grThru = clamp(-dot(grN, grL) * sign(dot(grN, grEye)),',
  '                       0.0, 1.0);',
  '  float grBeam = pow(clamp(dot(grEye, -grL) * 0.5 + 0.5, 0.0, 1.0),',
  '                     3.0);',
  '  vec3 grSap = astraHueShift(grC, -0.30) * 2.4;',
  // 0.75 is not a garnish. Once the shading normal faces the eye, a
  // backlit blade's DIFFUSE response is nearly zero — that is what
  // backlit means — so this term is not decorating the light on the
  // field, it IS the light on the field, exactly as it is in the
  // photograph. Weighted by the depth so the glow stops where the
  // sward closes over: a field that glows to its roots is fog.
  '  totalEmissiveRadiance += directionalLights[0].color * grSap',
  '      * (0.75 * grThru * grBeam * grDepth * grDepth);',
  '#endif',
  '#endif',
  '  diffuseColor.rgb = grC;',
].join('\n');

/**
 * The mat the blades stand in, carrying the same clump field.
 *
 * Without it a top view sees only blade TIPS and the field reads as a
 * scatter of dots; with it the ground is already grass-coloured where
 * the blades are dense and dry where they thin out, and the blades add
 * their silhouette to a surface that is continuous underneath.
 */
function swardMat(extent, density, lush, dry, clumpAt, ground) {
  const segs = Math.max(8, Math.min(96, Math.round(extent / 0.5)));
  const geom = new THREE.PlaneGeometry(extent, extent, segs, segs);
  geom.rotateX(-Math.PI / 2);
  const pos = geom.attributes.position;
  const clump = new Float32Array(pos.count);
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    // 12 mm of lift: level with the caller's ground it z-fights, higher
    // than that and the blades look planted in a tray.
    pos.setY(i, (ground ? ground(x, z) : 0) + 0.012);
    clump[i] = clumpAt(x, z);
  }
  pos.needsUpdate = true;
  geom.computeVertexNormals();
  geom.setAttribute('aClump', new THREE.BufferAttribute(clump, 1));

  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.95, metalness: 0, name: 'GrassSward',
    // Under the canopy, so it sees LESS sky than the blades standing in
    // it — the same missing-AO correction, one step further down,
    // because a blade's uniform is a compromise weighted to its tips
    // and this surface is the bottom of the sward.
    envMapIntensity: 0.4,
  });
  patchStandard(mat, {
    name: 'grass:sward',
    uniforms: {
      uGrassLush: { value: lush.clone() },
      uGrassDry: { value: dry.clone() },
      // Grain at the blade spacing: coarser and the mat reads as felt.
      uGrassGrain: { value: Math.max(1, Math.sqrt(density)) },
    },
    vertexHead: 'attribute float aClump;\nvarying vec3 vSward;',
    vertexBody:
        '  vSward = vec3((modelMatrix * vec4(transformed, 1.0)).xz, aClump);',
    fragmentHead: [
      'uniform vec3 uGrassLush;',
      'uniform vec3 uGrassDry;',
      'uniform float uGrassGrain;',
      'varying vec3 vSward;',
    ].join('\n'),
    fragmentBody: [
      '  float swN = astraFbm2(vSward.xy * 3.5, 3);',
      // Bare ground between tussocks is still GRASS ground: let it
      // fall to a quarter green, not to bald straw. The metre-scale
      // term is the SAME field the blades dry themselves by, with the
      // sign flipped because this one measures green and theirs
      // measures straw — the two have to name the same patch.
      '  float swC = clamp(0.24 + 0.86 * vSward.z + (swN - 0.45) * 0.3',
      '      - (astraFbm2(vSward.xy * 0.35 + 5.0, 2) - 0.375) * 0.7,',
      '                    0.0, 1.0);',
      '  vec3 swCol = mix(uGrassDry, uGrassLush, swC);',
      // The MAT is most of the pixels — blades are a thin overlay — so
      // per-blade hue never reaches the frame on its own: measured, a
      // 4x wider blade jitter moved the frame's hue spread 4.8 to 6.7
      // degrees against 25-45 in a photograph. A meadow is yellow-green
      // through blue-green to straw across half a metre, so the mat
      // carries that swing itself — and now at the same scale and
      // swing as the blades standing in it, so the two agree instead of
      // laying one hue field over a different one.
      '  swCol = astraHueBreak(swCol, vSward.xy, 0.55, 0.62);',
      // Tufts, not a wash: strokes at the blade spacing, their
      // direction wandering so nothing rules a line across the field.
      '  float swA = astraFbm2(vSward.xy * 0.5, 2) * 6.28;',
      '  float swU = dot(vSward.xy, vec2(cos(swA), sin(swA)));',
      // A THIN stroke: astraStroke's aa is clamped at 0.30 while its
      // self-kill runs w*1.5 to w*5, so a half-width past ~0.20
      // never vanishes and the tufts alias instead of dissolving.
      '  float swG = astraStroke(swU * uGrassGrain * 1.8, 0.10);',
      '  swCol *= 0.82 + 0.26 * swG;',
      // The mat lies UNDER a canopy, so it is always in shade, and
      // deep grass swallows what thin dry ground bounces back. Deeper
      // here than the reference set it: with no AO pass on this host,
      // the mat IS the field's dark interior — everything a photograph
      // of a meadow has between the blades comes from this line.
      '  swCol *= 0.66 - 0.30 * swC;',
      '  diffuseColor.rgb = swCol;',
    ].join('\n'),
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'Sward';
  mesh.receiveShadow = true;
  return mesh;
}
