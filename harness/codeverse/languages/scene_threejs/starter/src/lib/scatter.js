/**
 * Surface scatter engine: stamp one prototype across a mesh's actual
 * surface as a single InstancedMesh (one draw call). Closes
 * MeshSurfaceSampler's two traps: LOCAL-space samples (baked to world
 * here) and its Math.random default (seeded mulberry32 instead).
 * Caller contract: author `protoGeom` with its BASE at y = 0, and add
 * the returned mesh at the scene ROOT — matrices are WORLD space.
 */

import * as THREE from 'three';
import { MeshSurfaceSampler } from 'three/addons/math/MeshSurfaceSampler.js';
import { mulberry32 } from './noise.js';

// The scene contract's albedo band for a non-emissive surface. Colour
// jitter that multiplies a copy past the ceiling blows it out under
// ACES before the sun even lands on it; jitter that drops one under the
// floor punches a black hole in the field. Both ends are scaled back.
const ALBEDO_CEIL = 0.82;
const ALBEDO_FLOOR = 0.02;

/**
 * Scatter `count` copies of a prototype over a mesh's surface.
 *
 * Samples are area-weighted (big triangles get proportionally more
 * instances), seeded, and land exactly ON the surface in world space.
 * Each instance gets a random yaw plus uniform scale jitter; the whole
 * spread renders as one draw call.
 *
 * @param {THREE.Mesh} surface The mesh to scatter over (its current
 *   world transform is baked into the instances - transform it BEFORE
 *   calling, and add the returned mesh at the scene root).
 * @param {THREE.BufferGeometry} protoGeom Prototype geometry, base at
 *   y = 0.
 * @param {THREE.Material} protoMat Prototype material (shared by all
 *   instances; per-instance tint via `colorJitter` needs no clone).
 * @param {number} count Instances to place (capped at `maxCount`,
 *   default 1000 - overdraw is the one measured scatter footgun).
 * @param {object} [opts] Options:
 *   `alignToNormal` (default false) tilt each copy's +Y onto the
 *     surface normal - trees and posts stay upright (false), grass,
 *     rocks and moss follow the slope (true). A NUMBER 0..1 leans them
 *     part way: real grass on a bank leans downhill without lying flat,
 *     so 0.6-0.8 reads better than a hard 1 on anything that grows;
 *   `scaleJitter` (default 0.3) uniform scale spread, 1 +- jitter;
 *   `seed` (default 1) the mulberry32 seed - same seed, same field;
 *   `maxSlopeDeg` (default none) reject samples whose surface normal
 *     tilts more than this from vertical, keeping grass off cliff
 *     faces (rejected draws are re-sampled; if the surface cannot
 *     supply enough flat ground the mesh returns with fewer, drawn
 *     `.count` telling the truth);
 *   `colorJitter` (default 0) 0..1 per-instance colour variation
 *     multiplied onto the material colour via `setColorAt` - breaks
 *     the wallpaper read of identical copies. CENTRED on the authored
 *     albedo (a field's mean is the colour you picked) and it varies
 *     HUE, not just brightness: each copy is nudged along a warm/cool
 *     axis, which is what stops a thousand leaves reading as one flat
 *     green. The product is held inside the 0.02..0.82 albedo band;
 *   `hueJitter` (default `colorJitter * 0.8`) the warm/cool half-width
 *     on its own - raise it for foliage and lichened stone, drop it to
 *     0 for painted or dyed copies that must hold one hue. Capped by
 *     the albedo's own saturation, and asymmetric (the cool side runs
 *     at 0.45 of the warm side): both because a full symmetric axis on
 *     grey stone comes back half orange and half blue;
 *   `maxCount` (default 1000) the hard cap;
 *   `name` (default 'Scatter') the returned mesh's name.
 * @returns {THREE.InstancedMesh} One instanced mesh, world-space
 *   matrices, shadows on, ready to add at the scene root.
 */
export function scatter(surface, protoGeom, protoMat, count, opts = {}) {
  const align = opts.alignToNormal === true ? 1
      : (opts.alignToNormal || 0);
  const scaleJitter = opts.scaleJitter === undefined ? 0.3 : opts.scaleJitter;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const maxCount = opts.maxCount === undefined ? 1000 : opts.maxCount;
  const colorJitter = opts.colorJitter || 0;
  const base = protoMat && protoMat.color ? protoMat.color : null;
  // A SATURATED albedo can absorb a wide hue push - leaves really do
  // run lime to blue-green. A near-neutral one cannot: it has no hue to
  // nudge, so the same push INVENTS one and a grey stone field comes
  // back half orange and half blue. Cap the chroma the axis may add.
  const peakCh = base ? Math.max(base.r, base.g, base.b) : 1;
  const sat = base && peakCh > 0
      ? (peakCh - Math.min(base.r, base.g, base.b)) / peakCh
      : 1;
  const hueJitter = Math.min(
      opts.hueJitter === undefined ? colorJitter * 0.8 : opts.hueJitter,
      0.5 * sat + 0.06);
  const n = Math.max(0, Math.min(count, maxCount));
  const minNy = opts.maxSlopeDeg === undefined
      ? -Infinity
      : Math.cos(THREE.MathUtils.degToRad(opts.maxSlopeDeg));

  const rand = mulberry32(seed);
  // Hue rides its OWN stream, so adding hue variation to a field costs
  // the main stream nothing and cannot move a single instance.
  const crand = mulberry32((seed * 2654435761 + 0x9e3779b9) >>> 0);
  const sampler = new MeshSurfaceSampler(surface)
      .setRandomGenerator(rand)
      .build();
  // FACE normals, not vertex normals: smoothed vertex normals
  // under-report slope on fBm terrain, so the slope gate would lie.
  // Clearing normalAttribute flips the sampler to its face-normal path.
  sampler.normalAttribute = undefined;

  // The sampler works in the surface's LOCAL space; bake the world
  // transform so the instances hold true world positions.
  surface.updateWorldMatrix(true, false);
  const toWorld = surface.matrixWorld;
  const normalM = new THREE.Matrix3().getNormalMatrix(toWorld);

  const mesh = new THREE.InstancedMesh(protoGeom, protoMat, n);
  const pos = new THREE.Vector3();
  const nrm = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const IDENT = new THREE.Quaternion();
  const quat = new THREE.Quaternion();
  const yaw = new THREE.Quaternion();
  const scl = new THREE.Vector3();
  const m = new THREE.Matrix4();
  const tint = new THREE.Color();
  let placed = 0;
  // Slope rejection re-samples; the attempt budget keeps a surface
  // that is all cliff from looping forever.
  const attempts = Math.max(1, n) * 40;
  for (let a = 0; a < attempts && placed < n; a++) {
    sampler.sample(pos, nrm);
    nrm.applyMatrix3(normalM).normalize();
    if (nrm.y < minNy) continue;
    pos.applyMatrix4(toWorld);
    yaw.setFromAxisAngle(up, rand() * Math.PI * 2);
    if (align > 0) {
      quat.setFromUnitVectors(up, nrm);
      // a partial lean is a slerp from world-up towards the normal,
      // so 0.7 on a 40 deg bank stands the tuft at 28 deg, not flat
      if (align < 1) quat.slerp(IDENT, 1 - align).normalize();
      quat.multiply(yaw);
    } else {
      quat.copy(yaw);
    }
    const s = 1 + (rand() - 0.5) * 2 * scaleJitter;
    m.compose(pos, quat, scl.setScalar(s));
    mesh.setMatrixAt(placed, m);
    // Drawn whether or not it is USED: turning colorJitter on to break
    // a wallpaper read must not also re-roll the layout underneath it.
    const vr = rand();
    if (colorJitter > 0 || hueJitter > 0) {
      // Asymmetric on purpose: sun-bleached, dusty and dead go a long
      // way WARM, while the cool end is shade the lighting already
      // supplies - a symmetric axis turns half a stone field blue.
      let c = (crand() - 0.5) * 2 * hueJitter;   // + warm/dry, - cool
      if (c < 0) c *= 0.45;
      // Divide the hue axis back out of the multiplier's luma, so the
      // warm end is not also the bright end: value and hue vary
      // independently and the peak stays bounded by colorJitter alone.
      const v = (1 + (vr - 0.5) * 2 * colorJitter * 0.85)
          / (1 + 0.2477 * c);
      let r = v * (1 + c), g = v * (1 + c * 0.15), b = v * (1 - c);
      if (base) {
        const peak = Math.max(base.r * r, base.g * g, base.b * b);
        const k = peak > ALBEDO_CEIL ? ALBEDO_CEIL / peak
            : (peak > 0 && peak < ALBEDO_FLOOR ? ALBEDO_FLOOR / peak : 1);
        r *= k; g *= k; b *= k;
      }
      mesh.setColorAt(placed, tint.setRGB(r, g, b));
    }
    placed++;
  }
  mesh.count = placed;
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.frustumCulled = false;  // one bbox for the whole spread
  mesh.name = opts.name || 'Scatter';
  return mesh;
}
