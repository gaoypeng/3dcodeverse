// src/assets/pond_rock.js — asset "PondRock"
// Japanese garden weathered basalt boulder with moss patina and faceted contours.
// Sized 1.10 x 0.75 x 0.90 m (w x h x d), ground-seated at y = 0, centred at (0, 0).
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function createSculptedBoulderGeo(T, radius, detail, prng, scaleVec, flattenBottom = true) {
  const geo = new T.DodecahedronGeometry(radius, detail);
  const pos = geo.attributes.position;
  const v = new T.Vector3();

  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);

    // Weathered rock facets & asymmetric cleavage planes
    const f1 = Math.sin(v.x * 4.2 + v.y * 2.5) * 0.14;
    const f2 = Math.cos(v.z * 3.8 + v.x * 2.1) * 0.11;
    const f3 = Math.sin(v.y * 3.2 + v.z * 3.0) * 0.13;
    const micro = (prng() - 0.5) * 0.08;

    v.x += (f1 + micro) * radius;
    v.y += (f2 + micro * 0.6) * radius;
    v.z += (f3 + micro) * radius;

    // Directional proportions
    v.x *= scaleVec.x;
    v.y *= scaleVec.y;
    v.z *= scaleVec.z;

    // Flatten bottom slightly for stable ground contact
    if (flattenBottom && v.y < -radius * scaleVec.y * 0.35) {
      v.y = -radius * scaleVec.y * 0.35 + (v.y + radius * scaleVec.y * 0.35) * 0.25;
    }

    pos.setXYZ(i, v.x, v.y, v.z);
  }

  geo.computeVertexNormals();
  return geo;
}

function createMossLayerGeo(T, radius, detail, prng, scaleVec, yMinThreshold = 0.05) {
  // Generates curved moss caps that crown the upper facets
  const geo = new T.DodecahedronGeometry(radius * 1.025, detail);
  const pos = geo.attributes.position;
  const v = new T.Vector3();

  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);

    const f1 = Math.sin(v.x * 4.2 + v.y * 2.5) * 0.14;
    const f2 = Math.cos(v.z * 3.8 + v.x * 2.1) * 0.11;
    const f3 = Math.sin(v.y * 3.2 + v.z * 3.0) * 0.13;
    const micro = (prng() - 0.5) * 0.06;

    v.x += (f1 + micro) * radius;
    v.y += (f2 + micro * 0.6) * radius + 0.015; // slight upward cushion
    v.z += (f3 + micro) * radius;

    v.x *= scaleVec.x * 0.96;
    v.y *= scaleVec.y * 1.02;
    v.z *= scaleVec.z * 0.96;

    // Cut off lower half
    if (v.y < yMinThreshold) {
      v.y = yMinThreshold - 0.02;
      v.x *= 0.85;
      v.z *= 0.85;
    }

    pos.setXYZ(i, v.x, v.y, v.z);
  }

  geo.computeVertexNormals();
  return geo;
}

export function pondRockParts(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 42) + (opts.variant || 0) * 1013;
  const prng = mulberry32(seed);

  // Basalt materials
  const basaltPrimaryMat = new T.MeshStandardMaterial({
    color: 0x3d4145,
    roughness: 0.9,
    metalness: 0.06,
    flatShading: true,
  });

  const basaltSecondaryMat = new T.MeshStandardMaterial({
    color: 0x2b2e31,
    roughness: 0.94,
    metalness: 0.04,
    flatShading: true,
  });

  const basaltHighlightMat = new T.MeshStandardMaterial({
    color: 0x4f5459,
    roughness: 0.88,
    metalness: 0.08,
    flatShading: true,
  });

  // Moss patina materials
  const mossMat = new T.MeshStandardMaterial({
    color: 0x50692a,
    roughness: 0.96,
    metalness: 0.0,
    flatShading: true,
  });

  const mossDarkMat = new T.MeshStandardMaterial({
    color: 0x3d5020,
    roughness: 0.98,
    metalness: 0.0,
    flatShading: true,
  });

  // 1. Primary large basalt boulder (centre-left)
  const mainGeo = createSculptedBoulderGeo(T, 0.44, 1, prng, new T.Vector3(1.12, 0.88, 0.95));
  mainGeo.translate(-0.06, 0.36, 0.02);

  // 2. Companion boulder (right flank)
  const flankGeo = createSculptedBoulderGeo(T, 0.32, 1, prng, new T.Vector3(0.95, 0.78, 0.88));
  flankGeo.rotateY(0.75);
  flankGeo.translate(0.36, 0.24, -0.05);

  // 3. Low anchor stone (front right skirt)
  const anchorGeo = createSculptedBoulderGeo(T, 0.22, 1, prng, new T.Vector3(1.15, 0.65, 0.9));
  anchorGeo.rotateY(-0.6);
  anchorGeo.translate(0.18, 0.14, 0.26);

  // 4. Rear stabilizing stone
  const rearGeo = createSculptedBoulderGeo(T, 0.24, 1, prng, new T.Vector3(0.9, 0.7, 1.05));
  rearGeo.rotateY(1.4);
  rearGeo.translate(-0.28, 0.16, -0.22);

  // 5. Moss crowns
  const mossMain = createMossLayerGeo(T, 0.44, 1, prng, new T.Vector3(1.12, 0.88, 0.95), 0.22);
  mossMain.translate(-0.06, 0.36, 0.02);

  const mossFlank = createMossLayerGeo(T, 0.32, 1, prng, new T.Vector3(0.95, 0.78, 0.88), 0.15);
  mossFlank.rotateY(0.75);
  mossFlank.translate(0.36, 0.24, -0.05);

  return [
    { geometry: mainGeo, material: basaltPrimaryMat, castShadow: true, receiveShadow: true },
    { geometry: flankGeo, material: basaltSecondaryMat, castShadow: true, receiveShadow: true },
    { geometry: anchorGeo, material: basaltHighlightMat, castShadow: true, receiveShadow: true },
    { geometry: rearGeo, material: basaltSecondaryMat, castShadow: true, receiveShadow: true },
    { geometry: mossMain, material: mossMat, castShadow: false, receiveShadow: true },
    { geometry: mossFlank, material: mossDarkMat, castShadow: false, receiveShadow: true },
  ];
}

export function buildPondRock(T = THREE, opts = {}) {
  let threeLib = T;
  let options = opts;
  if (T && !T.Group && typeof T === 'object') {
    options = T;
    threeLib = THREE;
  }
  if (!threeLib) threeLib = THREE;

  const g = new threeLib.Group();
  g.name = 'PondRock';

  const parts = pondRockParts(threeLib, options);
  for (const p of parts) {
    const mesh = new threeLib.Mesh(p.geometry, p.material);
    mesh.castShadow = p.castShadow ?? true;
    mesh.receiveShadow = p.receiveShadow ?? true;
    g.add(mesh);
  }

  // Exact target size: 1.10 x 0.75 x 0.90 m
  const TARGET_W = 1.10;
  const TARGET_H = 0.75;
  const TARGET_D = 0.90;

  // Measure and align
  const box = new threeLib.Box3().setFromObject(g);
  const size = new threeLib.Vector3();
  box.getSize(size);

  if (size.x > 0.001 && size.y > 0.001 && size.z > 0.001) {
    const sx = TARGET_W / size.x;
    const sy = TARGET_H / size.y;
    const sz = TARGET_D / size.z;

    // Apply scale to geometries / children
    for (const child of g.children) {
      child.geometry.scale(sx, sy, sz);
    }

    // Recalculate box
    const adjustedBox = new threeLib.Box3().setFromObject(g);
    const center = new threeLib.Vector3();
    adjustedBox.getCenter(center);

    const shiftX = -center.x;
    const shiftY = -adjustedBox.min.y;
    const shiftZ = -center.z;

    for (const child of g.children) {
      child.geometry.translate(shiftX, shiftY, shiftZ);
    }
  }

  g.userData.size = [TARGET_W, TARGET_H, TARGET_D];
  g.userData.tick = (t, dt) => {};

  return g;
}
