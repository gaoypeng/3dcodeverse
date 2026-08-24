// src/assets/stepping_stone.js — asset "SteppingStone"
// Japanese garden flat river slate stepping stone (Tobi-ishi) embedded flush in path.
// Size: 0.65 x 0.12 x 0.55 m (w x h x d)
import * as THREE from 'three';

function createRng(seed = 42) {
  let s = (seed >>> 0) || 1;
  return function() {
    s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function steppingStoneParts(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 101) + (opts.variant !== undefined ? opts.variant * 37 : 0);
  const rng = createRng(seed);

  // 1. Main Slate Body
  // Create an organic slate cylinder with perturbed radial profile and stepped cleavage
  const radialSegments = 24;
  const heightSegments = 4;
  const rawRadius = 0.32;
  const rawHeight = 0.12;

  const slateGeo = new T.CylinderGeometry(rawRadius * 0.96, rawRadius, rawHeight, radialSegments, heightSegments, false);
  const pos = slateGeo.attributes.position;

  // Perturbation frequencies
  const f1 = 2.0 + Math.floor(rng() * 3);
  const p1 = rng() * Math.PI * 2;
  const f2 = 4.0;
  const p2 = rng() * Math.PI * 2;

  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i);
    let y = pos.getY(i);
    let z = pos.getZ(i);

    const angle = Math.atan2(z, x);
    const r = Math.sqrt(x * x + z * z);

    if (r > 0.02) {
      // Oval contour (wider in X, narrower in Z) + natural wavy slate fractures
      const contourMod = 1.0
        + 0.08 * Math.sin(angle * f1 + p1)
        + 0.04 * Math.cos(angle * f2 + p2)
        + (rng() - 0.5) * 0.02;

      const normX = Math.cos(angle) * (x > 0 ? 1 : 0.95);
      const normZ = Math.sin(angle) * (z > 0 ? 0.92 : 1.0);

      x = normX * r * 1.05 * contourMod;
      z = normZ * r * 0.88 * contourMod;

      // Subtle top bevel / natural slate layer stepping
      if (y > rawHeight * 0.3) {
        y += (Math.sin(x * 6.0 + p1) * 0.006 + Math.cos(z * 7.0 + p2) * 0.005);
      }
    } else {
      // Center top/bottom slight natural dome
      if (y > 0) {
        y += 0.004;
      }
    }

    pos.setXYZ(i, x, y, z);
  }

  slateGeo.computeVertexNormals();

  // Normalize bounding box to strictly fit target dimensions
  slateGeo.computeBoundingBox();
  const bb = slateGeo.boundingBox;
  const curW = bb.max.x - bb.min.x;
  const curH = bb.max.y - bb.min.y;
  const curD = bb.max.z - bb.min.z;

  const targetW = 0.65;
  const targetH = 0.12;
  const targetD = 0.55;

  const sx = targetW / curW;
  const sy = targetH / curH;
  const sz = targetD / curD;

  slateGeo.scale(sx, sy, sz);

  // Align base to y = 0 and center in XZ
  slateGeo.computeBoundingBox();
  const bb2 = slateGeo.boundingBox;
  const cx = (bb2.min.x + bb2.max.x) * 0.5;
  const cz = (bb2.min.z + bb2.max.z) * 0.5;
  slateGeo.translate(-cx, -bb2.min.y, -cz);

  const slateMat = new T.MeshStandardMaterial({
    color: 0x484c52,
    roughness: 0.78,
    metalness: 0.06,
    flatShading: false
  });

  // 2. Moss Patch / Lichen Patina on upper-edge crevices
  const mossGeo = new T.CylinderGeometry(0.18, 0.22, 0.015, 14);
  const mPos = mossGeo.attributes.position;
  for (let i = 0; i < mPos.count; i++) {
    let mx = mPos.getX(i);
    let my = mPos.getY(i);
    let mz = mPos.getZ(i);
    const mAngle = Math.atan2(mz, mx);
    const mr = Math.sqrt(mx * mx + mz * mz);
    if (mr > 0.02) {
      const distort = 1.0 + 0.25 * Math.sin(mAngle * 3.0 + 1.2) + 0.15 * Math.cos(mAngle * 5.0);
      mx *= distort * 0.85;
      mz *= distort * 0.65;
    }
    mPos.setXYZ(i, mx, my, mz);
  }
  mossGeo.computeVertexNormals();
  // Place moss embedded on one corner edge flush with stone top
  mossGeo.scale(0.8, 0.8, 0.8);
  mossGeo.translate(0.14, targetH - 0.003, -0.11);

  const mossMat = new T.MeshStandardMaterial({
    color: 0x42522c,
    roughness: 0.95,
    metalness: 0.02
  });

  // 3. Natural weathered cleft flake (accent layer for realistic slate texture)
  const cleftGeo = new T.CylinderGeometry(0.24, 0.26, 0.008, 16);
  cleftGeo.scale(0.9, 1.0, 0.7);
  cleftGeo.translate(-0.06, targetH - 0.002, 0.04);
  const cleftMat = new T.MeshStandardMaterial({
    color: 0x54585f,
    roughness: 0.70,
    metalness: 0.08
  });

  return [
    { geometry: slateGeo, material: slateMat, castShadow: true, receiveShadow: true },
    { geometry: cleftGeo, material: cleftMat, castShadow: true, receiveShadow: true },
    { geometry: mossGeo, material: mossMat, castShadow: true, receiveShadow: true }
  ];
}

export function buildSteppingStone(T = THREE, opts = {}) {
  // Handle flexible invocation: buildSteppingStone(THREE, opts) or buildSteppingStone(opts)
  let ThreeLib = THREE;
  let options = opts;
  if (T && T.Vector3) {
    ThreeLib = T;
  } else if (typeof T === 'object') {
    options = T;
    ThreeLib = THREE;
  }

  const group = new ThreeLib.Group();
  group.name = 'SteppingStone';

  const parts = steppingStoneParts(ThreeLib, options);
  for (const p of parts) {
    const mesh = new ThreeLib.Mesh(p.geometry, p.material);
    mesh.castShadow = p.castShadow ?? true;
    mesh.receiveShadow = p.receiveShadow ?? true;
    group.add(mesh);
  }

  group.userData.size = [0.65, 0.12, 0.55];
  return group;
}
