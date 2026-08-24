// src/assets/japanese_maple.js — asset "JapaneseMaple": Gnarled dark bark trunk splitting into delicate spreading horizontal branches with dense clusters of red and orange palmate maple leaves.
// Size: 4.00 x 4.50 x 4.00 m (w x h x d)
// CONTRACT: export function buildJapaneseMaple(THREE, opts = {}) → THREE.Group
import * as THREE from 'three';

// Deterministic PRNG
function createRng(seed = 42) {
  let s = (seed | 0) + 0x6D2B79F5;
  return function() {
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    s = (s + 0x6D2B79F5) | 0;
    return ((t >>> 0) / 4294967296);
  };
}

export function buildJapaneseMaple(T = THREE, opts = {}) {
  const seed = opts.seed !== undefined ? opts.seed : 42;
  const rng = createRng(seed);

  const group = new T.Group();
  group.name = 'JapaneseMaple';

  // Materials
  // Bark: dark gnarled brownish-grey bark
  const barkMat = new T.MeshStandardMaterial({
    color: 0x2e231c,
    roughness: 0.92,
    metalness: 0.08
  });

  // Autumn Foliage Materials: rich scarlet red, deep crimson, and vibrant orange-red
  const leafMatCrimson = new T.MeshStandardMaterial({
    color: 0x9b1b1b,
    roughness: 0.65,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMatScarlet = new T.MeshStandardMaterial({
    color: 0xc4281b,
    roughness: 0.6,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMatOrange = new T.MeshStandardMaterial({
    color: 0xd95a16,
    roughness: 0.65,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMaterials = [leafMatCrimson, leafMatScarlet, leafMatOrange];

  // Helper to build a curved branch/trunk segment using CatmullRomCurve3 and TubeGeometry
  function createBranch(points, radiusStart, radiusEnd, segments = 10, radialSegments = 8) {
    const curve = new T.CatmullRomCurve3(points);
    // Custom geometry with tapering radius along the curve
    const frames = curve.computeFrenetFrames(segments, false);
    const geometry = new T.BufferGeometry();
    const vertices = [];
    const normals = [];
    const indices = [];

    for (let i = 0; i <= segments; i++) {
      const u = i / segments;
      const point = curve.getPointAt(u);
      const r = radiusStart * (1 - u) + radiusEnd * u;
      const N = frames.normals[i];
      const B = frames.binormals[i];

      for (let j = 0; j <= radialSegments; j++) {
        const v = (j / radialSegments) * Math.PI * 2;
        const sin = Math.sin(v);
        const cos = -Math.cos(v);

        const normal = new T.Vector3()
          .addScaledVector(N, cos)
          .addScaledVector(B, sin)
          .normalize();

        const vertex = new T.Vector3()
          .copy(point)
          .addScaledVector(normal, r);

        vertices.push(vertex.x, vertex.y, vertex.z);
        normals.push(normal.x, normal.y, normal.z);
      }
    }

    for (let i = 0; i < segments; i++) {
      for (let j = 0; j < radialSegments; j++) {
        const a = i * (radialSegments + 1) + j;
        const b = (i + 1) * (radialSegments + 1) + j;
        const c = (i + 1) * (radialSegments + 1) + (j + 1);
        const d = i * (radialSegments + 1) + (j + 1);

        indices.push(a, b, d);
        indices.push(b, c, d);
      }
    }

    geometry.setAttribute('position', new T.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute('normal', new T.Float32BufferAttribute(normals, 3));
    geometry.setIndex(indices);

    const mesh = new T.Mesh(geometry, barkMat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
    return mesh;
  }

  // --- TRUNK BASE & ROOTS ---
  // Flared root base at ground y=0
  const rootBaseGeo = new T.CylinderGeometry(0.24, 0.42, 0.45, 10);
  rootBaseGeo.translate(0, 0.22, 0);
  const rootBase = new T.Mesh(rootBaseGeo, barkMat);
  rootBase.castShadow = true;
  rootBase.receiveShadow = true;
  group.add(rootBase);

  // Surface roots spreading into ground
  const rootAngles = [0.3, 1.8, 3.2, 4.7, 5.8];
  for (let i = 0; i < rootAngles.length; i++) {
    const ang = rootAngles[i] + (rng() - 0.5) * 0.3;
    const len = 0.6 + rng() * 0.3;
    createBranch([
      new T.Vector3(0.12 * Math.cos(ang), 0.25, 0.12 * Math.sin(ang)),
      new T.Vector3(0.35 * Math.cos(ang), 0.1, 0.35 * Math.sin(ang)),
      new T.Vector3(len * Math.cos(ang), 0.02, len * Math.sin(ang))
    ], 0.1, 0.03, 6, 6);
  }

  // Gnarled lower trunk (twisting up to ~1.7m)
  createBranch([
    new T.Vector3(0, 0.3, 0),
    new T.Vector3(0.08, 0.8, -0.05),
    new T.Vector3(-0.06, 1.3, 0.08),
    new T.Vector3(0.02, 1.75, 0.02)
  ], 0.24, 0.17, 8, 8);

  // --- MAIN SCAFFOLD BRANCHES ---
  // Japanese maples characteristically have 4-5 major spreading, twisting boughs
  // Primary branch 1: reaching +X, +Z (low spreading horizontal)
  createBranch([
    new T.Vector3(0.02, 1.75, 0.02),
    new T.Vector3(0.45, 2.1, 0.35),
    new T.Vector3(1.1, 2.35, 0.8),
    new T.Vector3(1.6, 2.5, 1.25)
  ], 0.14, 0.07, 8, 7);

  // Primary branch 2: reaching -X, +Z (high spreading arch)
  createBranch([
    new T.Vector3(0.02, 1.75, 0.02),
    new T.Vector3(-0.35, 2.2, 0.25),
    new T.Vector3(-0.95, 2.7, 0.65),
    new T.Vector3(-1.55, 2.85, 0.95)
  ], 0.13, 0.065, 8, 7);

  // Primary branch 3: reaching -X, -Z (graceful weeping spread)
  createBranch([
    new T.Vector3(0.02, 1.75, 0.02),
    new T.Vector3(-0.4, 2.3, -0.35),
    new T.Vector3(-1.05, 2.8, -0.85),
    new T.Vector3(-1.5, 3.1, -1.35)
  ], 0.13, 0.065, 8, 7);

  // Primary branch 4: reaching +X, -Z (wide horizontal canopy arm)
  createBranch([
    new T.Vector3(0.02, 1.75, 0.02),
    new T.Vector3(0.35, 2.25, -0.4),
    new T.Vector3(0.95, 2.85, -0.8),
    new T.Vector3(1.5, 3.0, -1.25)
  ], 0.12, 0.06, 8, 7);

  // Primary branch 5: central ascending crown leader
  createBranch([
    new T.Vector3(0.02, 1.75, 0.02),
    new T.Vector3(-0.05, 2.45, 0.05),
    new T.Vector3(0.15, 3.2, -0.1),
    new T.Vector3(0.1, 3.85, 0.1)
  ], 0.13, 0.06, 8, 7);

  // --- SECONDARY BRANCHES ---
  const subBranches = [
    // off branch 1
    { pts: [new T.Vector3(1.1, 2.35, 0.8), new T.Vector3(1.45, 2.25, 0.4), new T.Vector3(1.75, 2.35, 0.1)], r0: 0.06, r1: 0.035 },
    { pts: [new T.Vector3(1.6, 2.5, 1.25), new T.Vector3(1.8, 2.35, 1.55), new T.Vector3(1.85, 2.2, 1.75)], r0: 0.05, r1: 0.025 },
    { pts: [new T.Vector3(1.6, 2.5, 1.25), new T.Vector3(1.7, 2.75, 1.2), new T.Vector3(1.85, 2.9, 1.0)], r0: 0.05, r1: 0.025 },

    // off branch 2
    { pts: [new T.Vector3(-0.95, 2.7, 0.65), new T.Vector3(-1.35, 2.55, 0.25), new T.Vector3(-1.75, 2.6, -0.1)], r0: 0.06, r1: 0.03 },
    { pts: [new T.Vector3(-1.55, 2.85, 0.95), new T.Vector3(-1.8, 2.7, 1.35), new T.Vector3(-1.85, 2.5, 1.65)], r0: 0.05, r1: 0.025 },
    { pts: [new T.Vector3(-1.55, 2.85, 0.95), new T.Vector3(-1.7, 3.2, 0.9), new T.Vector3(-1.8, 3.45, 0.7)], r0: 0.045, r1: 0.02 },

    // off branch 3
    { pts: [new T.Vector3(-1.05, 2.8, -0.85), new T.Vector3(-0.7, 3.05, -1.35), new T.Vector3(-0.6, 3.2, -1.75)], r0: 0.055, r1: 0.025 },
    { pts: [new T.Vector3(-1.5, 3.1, -1.35), new T.Vector3(-1.75, 2.9, -1.65), new T.Vector3(-1.85, 2.7, -1.8)], r0: 0.05, r1: 0.025 },
    { pts: [new T.Vector3(-1.5, 3.1, -1.35), new T.Vector3(-1.6, 3.5, -1.1), new T.Vector3(-1.65, 3.8, -0.8)], r0: 0.045, r1: 0.02 },

    // off branch 4
    { pts: [new T.Vector3(0.95, 2.85, -0.8), new T.Vector3(1.4, 2.7, -0.5), new T.Vector3(1.75, 2.8, -0.2)], r0: 0.055, r1: 0.025 },
    { pts: [new T.Vector3(1.5, 3.0, -1.25), new T.Vector3(1.8, 2.85, -1.55), new T.Vector3(1.85, 2.65, -1.75)], r0: 0.05, r1: 0.025 },
    { pts: [new T.Vector3(1.5, 3.0, -1.25), new T.Vector3(1.6, 3.4, -1.1), new T.Vector3(1.65, 3.7, -0.75)], r0: 0.045, r1: 0.02 },

    // off crown branch 5
    { pts: [new T.Vector3(0.15, 3.2, -0.1), new T.Vector3(0.55, 3.45, 0.35), new T.Vector3(0.85, 3.65, 0.6)], r0: 0.05, r1: 0.025 },
    { pts: [new T.Vector3(0.15, 3.2, -0.1), new T.Vector3(-0.45, 3.5, -0.25), new T.Vector3(-0.75, 3.7, -0.4)], r0: 0.05, r1: 0.025 },
    { pts: [new T.Vector3(0.1, 3.85, 0.1), new T.Vector3(0.3, 4.15, 0.2), new T.Vector3(0.2, 4.35, 0.05)], r0: 0.045, r1: 0.02 },
    { pts: [new T.Vector3(0.1, 3.85, 0.1), new T.Vector3(-0.25, 4.1, -0.15), new T.Vector3(-0.15, 4.3, -0.1)], r0: 0.045, r1: 0.02 }
  ];

  for (const b of subBranches) {
    createBranch(b.pts, b.r0, b.r1, 6, 6);
  }

  // --- MAPLE FOLIAGE CLUSTERS ---
  // Japanese maples have horizontal layered pads / canopies of palmate leaves.
  // We model these as flattened domes/ellipsoids with star-like/palmate faceted geometry and layered plates.
  
  // Create a palmate leaf clump geometry (layered flattened disc with multi-lobed edge)
  function createFoliageClumpGeo(radiusX, radiusY, radiusZ) {
    const geo = new T.SphereGeometry(1, 10, 6);
    geo.scale(radiusX, radiusY, radiusZ);
    // Displace vertices slightly for organic jagged foliage edge
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const vx = pos.getX(i);
      const vy = pos.getY(i);
      const vz = pos.getZ(i);
      const angle = Math.atan2(vz, vx);
      // 5-lobed palmate modulation
      const lobe = 1.0 + 0.18 * Math.sin(angle * 5.0) + 0.1 * Math.cos(angle * 3.0);
      pos.setXYZ(i, vx * lobe, vy * (0.8 + 0.3 * Math.sin(angle * 2.0)), vz * lobe);
    }
    geo.computeVertexNormals();
    return geo;
  }

  // Define foliage cluster locations around the canopy envelope (width ~4m, height ~4.5m, depth ~4m)
  const foliageNodes = [
    // Top Central Crown
    { pos: [0.1, 4.35, 0.0], sx: 0.85, sy: 0.35, sz: 0.85, mat: leafMatScarlet, rx: 0.05, rz: -0.05 },
    { pos: [0.3, 4.1, 0.25], sx: 0.75, sy: 0.32, sz: 0.7, mat: leafMatCrimson, rx: 0.1, rz: 0.08 },
    { pos: [-0.3, 4.15, -0.2], sx: 0.7, sy: 0.3, sz: 0.75, mat: leafMatOrange, rx: -0.08, rz: -0.1 },
    { pos: [0.65, 3.8, 0.45], sx: 0.75, sy: 0.32, sz: 0.7, mat: leafMatScarlet, rx: 0.12, rz: 0.15 },
    { pos: [-0.6, 3.85, -0.35], sx: 0.7, sy: 0.3, sz: 0.7, mat: leafMatCrimson, rx: -0.15, rz: -0.12 },

    // High Spreading Layer (Y ≈ 3.2 - 3.7)
    { pos: [-1.4, 3.65, -0.9], sx: 0.95, sy: 0.36, sz: 0.9, mat: leafMatCrimson, rx: -0.15, rz: -0.2 },
    { pos: [-1.75, 3.4, -0.7], sx: 0.8, sy: 0.32, sz: 0.8, mat: leafMatScarlet, rx: -0.1, rz: -0.25 },
    { pos: [1.4, 3.55, -0.85], sx: 0.9, sy: 0.35, sz: 0.85, mat: leafMatOrange, rx: -0.15, rz: 0.2 },
    { pos: [1.7, 3.35, -0.65], sx: 0.8, sy: 0.3, sz: 0.75, mat: leafMatScarlet, rx: -0.1, rz: 0.25 },
    { pos: [-1.65, 3.45, 0.75], sx: 0.9, sy: 0.35, sz: 0.85, mat: leafMatScarlet, rx: 0.15, rz: -0.2 },
    { pos: [-1.2, 3.65, 0.55], sx: 0.85, sy: 0.32, sz: 0.8, mat: leafMatOrange, rx: 0.12, rz: -0.15 },
    { pos: [1.2, 3.55, 0.65], sx: 0.85, sy: 0.34, sz: 0.85, mat: leafMatCrimson, rx: 0.15, rz: 0.18 },

    // Mid Spreading Layer (Y ≈ 2.7 - 3.2)
    { pos: [-1.55, 3.1, -1.45], sx: 1.0, sy: 0.38, sz: 0.95, mat: leafMatScarlet, rx: -0.2, rz: -0.2 },
    { pos: [-1.85, 2.85, -1.65], sx: 0.8, sy: 0.3, sz: 0.75, mat: leafMatCrimson, rx: -0.22, rz: -0.25 },
    { pos: [-0.65, 3.25, -1.65], sx: 0.85, sy: 0.32, sz: 0.8, mat: leafMatOrange, rx: -0.25, rz: -0.05 },
    { pos: [1.5, 3.05, -1.35], sx: 0.95, sy: 0.36, sz: 0.95, mat: leafMatScarlet, rx: -0.18, rz: 0.2 },
    { pos: [1.8, 2.8, -1.6], sx: 0.8, sy: 0.3, sz: 0.75, mat: leafMatCrimson, rx: -0.2, rz: 0.25 },
    { pos: [1.65, 2.95, 0.95], sx: 0.9, sy: 0.35, sz: 0.85, mat: leafMatOrange, rx: 0.2, rz: 0.18 },
    { pos: [-1.6, 2.95, 1.1], sx: 0.95, sy: 0.36, sz: 0.9, mat: leafMatScarlet, rx: 0.22, rz: -0.18 },
    { pos: [-1.8, 2.75, 1.45], sx: 0.8, sy: 0.32, sz: 0.75, mat: leafMatCrimson, rx: 0.25, rz: -0.22 },

    // Low Weeping / Extended Tier (Y ≈ 2.2 - 2.7)
    { pos: [1.65, 2.55, 1.35], sx: 0.95, sy: 0.35, sz: 0.9, mat: leafMatCrimson, rx: 0.2, rz: 0.22 },
    { pos: [1.85, 2.35, 1.65], sx: 0.75, sy: 0.28, sz: 0.7, mat: leafMatScarlet, rx: 0.25, rz: 0.25 },
    { pos: [1.75, 2.45, 0.15], sx: 0.85, sy: 0.32, sz: 0.8, mat: leafMatOrange, rx: 0.05, rz: 0.25 },
    { pos: [-1.7, 2.65, -0.1], sx: 0.85, sy: 0.32, sz: 0.8, mat: leafMatScarlet, rx: -0.05, rz: -0.25 },
    { pos: [-1.8, 2.5, 1.6], sx: 0.75, sy: 0.28, sz: 0.7, mat: leafMatOrange, rx: 0.22, rz: -0.25 },
    { pos: [1.8, 2.7, -0.25], sx: 0.8, sy: 0.3, sz: 0.75, mat: leafMatCrimson, rx: -0.05, rz: 0.25 },
    { pos: [-1.8, 2.75, -1.75], sx: 0.75, sy: 0.28, sz: 0.7, mat: leafMatScarlet, rx: -0.22, rz: -0.25 }
  ];

  const foliageGroup = new T.Group();
  foliageGroup.name = 'FoliageCanopy';

  for (let i = 0; i < foliageNodes.length; i++) {
    const node = foliageNodes[i];
    const clumpGeo = createFoliageClumpGeo(node.sx, node.sy, node.sz);
    const clump = new T.Mesh(clumpGeo, node.mat);
    clump.position.set(node.pos[0], node.pos[1], node.pos[2]);
    clump.rotation.set(node.rx, rng() * Math.PI * 2, node.rz);
    clump.castShadow = true;
    clump.receiveShadow = true;
    foliageGroup.add(clump);

    // Add a secondary slightly offset sub-tier plate for leafy density
    const subGeo = createFoliageClumpGeo(node.sx * 0.7, node.sy * 0.7, node.sz * 0.7);
    const subMat = leafMaterials[(i + 1) % leafMaterials.length];
    const subClump = new T.Mesh(subGeo, subMat);
    subClump.position.set(
      node.pos[0] + (rng() - 0.5) * 0.25,
      node.pos[1] - 0.12,
      node.pos[2] + (rng() - 0.5) * 0.25
    );
    subClump.rotation.set(node.rx * 1.1, rng() * Math.PI * 2, node.rz * 1.1);
    subClump.castShadow = true;
    subClump.receiveShadow = true;
    foliageGroup.add(subClump);
  }

  group.add(foliageGroup);

  // Set explicit bounding size: width 4.0m, height 4.5m, depth 4.0m
  group.userData.size = [4.0, 4.5, 4.0];

  // Subtle wind sway animation hook
  group.userData.tick = (t) => {
    const sway = Math.sin(t * 1.8 + seed) * 0.02;
    foliageGroup.rotation.z = sway;
    foliageGroup.rotation.x = Math.cos(t * 1.4 + seed) * 0.015;
  };

  return group;
}
