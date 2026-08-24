// src/assets/bamboo_fence.js — Takegaki traditional Japanese bamboo fence section
// Size: 3.00m (w) x 2.10m (h) x 0.15m (d)
// Origin at the base (y = 0), +Y up, +Z front, centred on Y axis.
import * as THREE from 'three';

function mulberry32(seed) {
  let a = seed >>> 0;
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildBambooFence(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 42) + (opts.variant || 0) * 1013;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'BambooFence';

  // --- Materials ---
  const bambooMat1 = new T.MeshStandardMaterial({
    color: 0xc8aa60,
    roughness: 0.55,
    metalness: 0.05,
  });
  const bambooMat2 = new T.MeshStandardMaterial({
    color: 0xbaa054,
    roughness: 0.58,
    metalness: 0.05,
  });
  const nodeMat = new T.MeshStandardMaterial({
    color: 0x8a7038,
    roughness: 0.75,
    metalness: 0.05,
  });
  const postMat = new T.MeshStandardMaterial({
    color: 0x3e2d1d,
    roughness: 0.88,
    metalness: 0.02,
  });
  const postCapMat = new T.MeshStandardMaterial({
    color: 0x2b1e13,
    roughness: 0.7,
    metalness: 0.1,
  });
  const twineMat = new T.MeshStandardMaterial({
    color: 0x1f1d1a,
    roughness: 0.96,
    metalness: 0.01,
  });
  const topCopingMat = new T.MeshStandardMaterial({
    color: 0xc2a45a,
    roughness: 0.5,
    metalness: 0.05,
  });
  const stoneBaseMat = new T.MeshStandardMaterial({
    color: 0x6e6e6a,
    roughness: 0.92,
    metalness: 0.02,
  });

  // Helper to add mesh with shadows
  function addMesh(geo, mat, x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0, parent = group) {
    const mesh = new T.Mesh(geo, mat);
    mesh.position.set(x, y, z);
    if (rx || ry || rz) mesh.rotation.set(rx, ry, rz);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  // --- 1. Stone foundation footers & Main vertical support posts (3 posts: Left, Center, Right) ---
  const postPositionsX = [-1.43, 0.0, 1.43];
  const postHeight = 2.04;
  const postRadius = 0.042;

  postPositionsX.forEach((px) => {
    // Stone plinth at base
    const plinthGeo = new T.CylinderGeometry(0.062, 0.07, 0.04, 8);
    addMesh(plinthGeo, stoneBaseMat, px, 0.02, 0);

    // Main wooden post
    const postGeo = new T.CylinderGeometry(postRadius, postRadius, postHeight, 14);
    addMesh(postGeo, postMat, px, postHeight / 2 + 0.01, 0);

    // Post wooden cap
    const capGeo = new T.ConeGeometry(0.05, 0.045, 14);
    addMesh(capGeo, postCapMat, px, postHeight + 0.032, 0);
  });

  // --- 2. Horizontal support rails (Split bamboo / timber stringers at 3 height levels, front & back) ---
  const railYLevels = [0.28, 1.05, 1.76];
  const railLength = 2.96;
  const railRadius = 0.018;

  railYLevels.forEach((ry, rIdx) => {
    // Front horizontal rail
    const frontRailGeo = new T.CylinderGeometry(railRadius, railRadius, railLength, 12);
    frontRailGeo.rotateZ(Math.PI / 2);
    addMesh(frontRailGeo, rIdx % 2 === 0 ? bambooMat1 : bambooMat2, 0, ry, 0.032);

    // Back horizontal rail
    const backRailGeo = new T.CylinderGeometry(railRadius, railRadius, railLength, 12);
    backRailGeo.rotateZ(Math.PI / 2);
    addMesh(backRailGeo, rIdx % 2 === 0 ? bambooMat2 : bambooMat1, 0, ry, -0.032);
  });

  // --- 3. Densely packed vertical bamboo canes ---
  const caneCount = 44;
  const startX = -1.37;
  const endX = 1.37;
  const stepX = (endX - startX) / (caneCount - 1);
  const caneRadius = 0.0175;
  const baseCaneHeight = 2.00;

  for (let i = 0; i < caneCount; i++) {
    // Avoid clipping directly inside the 3 main posts
    const cx = startX + i * stepX + (rand() - 0.5) * 0.005;
    if (Math.abs(cx - (-1.43)) < 0.035 || Math.abs(cx - 0.0) < 0.035 || Math.abs(cx - 1.43) < 0.035) {
      continue;
    }

    const caneH = baseCaneHeight + (rand() - 0.5) * 0.02;
    const caneMat = rand() > 0.5 ? bambooMat1 : bambooMat2;

    const caneGeo = new T.CylinderGeometry(caneRadius, caneRadius * 1.02, caneH, 10);
    addMesh(caneGeo, caneMat, cx, caneH / 2, (rand() - 0.5) * 0.004);

    // Bamboo nodes (rings along each vertical cane)
    const nodeOffsets = [0.35, 0.72, 1.12, 1.48, 1.82];
    for (const no of nodeOffsets) {
      const ny = no + (rand() - 0.5) * 0.04;
      if (ny > 0.08 && ny < caneH - 0.05) {
        const ringGeo = new T.TorusGeometry(caneRadius * 1.05, 0.0035, 6, 10);
        ringGeo.rotateX(Math.PI / 2);
        addMesh(ringGeo, nodeMat, cx, ny, 0);
      }
    }
  }

  // --- 4. Top bamboo coping / roof capping rail (Kasagi) ---
  // Thick protective split bamboo cap spanning the top of the fence (exact 3.00m length)
  const capLength = 3.00;
  const capRadius = 0.045;
  // Half cylinder / arched top cap
  const topCapGeo = new T.CylinderGeometry(capRadius, capRadius, capLength, 14, 1, false, 0, Math.PI);
  topCapGeo.rotateZ(Math.PI / 2);
  topCapGeo.rotateX(Math.PI);
  addMesh(topCapGeo, topCopingMat, 0, 2.055, 0);

  // End caps for the top coping
  const endCapGeo = new T.CylinderGeometry(0.0455, 0.0455, 0.02, 12);
  endCapGeo.rotateZ(Math.PI / 2);
  addMesh(endCapGeo, postCapMat, -1.49, 2.055, 0);
  addMesh(endCapGeo, postCapMat, 1.49, 2.055, 0);

  // --- 5. Traditional dark hemp twine lashings (Otoko-musubi knots) ---
  // A. Heavy cross-lashings at post-rail junctions
  postPositionsX.forEach((px) => {
    railYLevels.forEach((ry) => {
      // Cross wraps (X-lashing)
      const tie1Geo = new T.TorusGeometry(0.052, 0.0055, 6, 12);
      tie1Geo.rotateX(Math.PI / 2);
      tie1Geo.rotateY(0.4);
      addMesh(tie1Geo, twineMat, px, ry, 0);

      const tie2Geo = new T.TorusGeometry(0.052, 0.0055, 6, 12);
      tie2Geo.rotateX(Math.PI / 2);
      tie2Geo.rotateY(-0.4);
      addMesh(tie2Geo, twineMat, px, ry, 0);

      // Central knot nub
      const knotGeo = new T.SphereGeometry(0.011, 6, 6);
      addMesh(knotGeo, twineMat, px, ry, 0.052);
      addMesh(knotGeo, twineMat, px, ry, -0.052);
    });
  });

  // B. Regular twine ties securing canes to horizontal rails
  for (let k = -1.3; k <= 1.35; k += 0.22) {
    // Skip if near post
    if (Math.abs(k - (-1.43)) < 0.1 || Math.abs(k - 0.0) < 0.1 || Math.abs(k - 1.43) < 0.1) continue;

    railYLevels.forEach((ry) => {
      const wrapGeo = new T.TorusGeometry(0.04, 0.004, 6, 10);
      wrapGeo.rotateX(Math.PI / 2);
      addMesh(wrapGeo, twineMat, k, ry, 0);
    });
  }

  // C. Top cap retaining twine wraps
  const topTieX = [-1.35, -0.9, -0.45, 0.45, 0.9, 1.35];
  topTieX.forEach((tx) => {
    const topWrapGeo = new T.TorusGeometry(0.05, 0.0045, 6, 12);
    topWrapGeo.rotateZ(Math.PI / 2);
    addMesh(topWrapGeo, twineMat, tx, 2.055, 0);
  });

  group.userData.size = [3.0, 2.1, 0.15];
  group.userData.tick = (t, dt) => {
    // Static architectural asset
  };

  return group;
}

export function bambooFenceParts(T = THREE) {
  // Common parts export for instancing if required
  const group = buildBambooFence(T);
  const parts = [];
  group.traverse((obj) => {
    if (obj.isMesh) {
      parts.push({
        geometry: obj.geometry,
        material: obj.material,
        castShadow: true,
      });
    }
  });
  return parts;
}
