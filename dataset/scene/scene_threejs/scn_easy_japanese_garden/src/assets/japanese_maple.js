// src/assets/japanese_maple.js — asset "JapaneseMaple": Gnarled multi-stemmed Acer palmatum tree with sweeping horizontal branches and delicate canopy clusters of fiery red and crimson foliage.
// approx size 4.6 x 4.2 x 4.4 m (w x h x d)
// CONTRACT: export function buildJapaneseMaple(THREE, opts = {}) → THREE.Group, origin at base (y = 0), +Y up, +Z front, meters.
import * as THREE from 'three';

// Tiny deterministic PRNG
function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildJapaneseMaple(T = THREE, opts = {}) {
  const seed = opts.seed !== undefined ? opts.seed : 42;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'JapaneseMaple';

  // Materials
  const barkMat = new T.MeshStandardMaterial({
    color: 0x3d2b1f,
    roughness: 0.85,
    metalness: 0.05
  });

  const trunkDetailMat = new T.MeshStandardMaterial({
    color: 0x4a3525,
    roughness: 0.9,
    metalness: 0.05
  });

  // Autumn foliage shades: crimson, scarlet, ruby red, warm orange-red
  const leafMatCrimson = new T.MeshStandardMaterial({
    color: 0x9b111e,
    roughness: 0.7,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMatScarlet = new T.MeshStandardMaterial({
    color: 0xc8251e,
    roughness: 0.65,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMatRuby = new T.MeshStandardMaterial({
    color: 0xb31b1b,
    roughness: 0.65,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMatOrangeRed = new T.MeshStandardMaterial({
    color: 0xd34817,
    roughness: 0.7,
    metalness: 0.05,
    side: T.DoubleSide
  });

  const leafMaterials = [leafMatCrimson, leafMatScarlet, leafMatRuby, leafMatOrangeRed];

  // Helper: create smooth curved branch using CatmullRomCurve3 and TubeGeometry
  function createBranch(points, radiusStart, radiusEnd, segments = 10, radialSegments = 7, material = barkMat) {
    const curve = new T.CatmullRomCurve3(points);
    // Variable radius tube geometry approximation via segments
    const tubeGeo = new T.TubeGeometry(curve, segments, radiusStart, radialSegments, false);
    
    // Taper the tube towards the end
    const pos = tubeGeo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const u = (i / radialSegments | 0) / segments; // factor along curve 0..1
      const factor = THREE.MathUtils.lerp(1.0, radiusEnd / radiusStart, u);
      // get local offset from center if needed, or simply scale relative to spline center
      // Since TubeGeometry standard vertices lie around central spine points:
      const pOnSpine = curve.getPointAt(Math.min(Math.max(u, 0), 1));
      pos.setXYZ(
        i,
        pOnSpine.x + (pos.getX(i) - pOnSpine.x) * factor,
        pOnSpine.y + (pos.getY(i) - pOnSpine.y) * factor,
        pOnSpine.z + (pos.getZ(i) - pOnSpine.z) * factor
      );
    }
    tubeGeo.computeVertexNormals();
    const mesh = new T.Mesh(tubeGeo, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
    return mesh;
  }

  // --- TRUNK BASE & ROOTS (sitting on y=0) ---
  // Root flare / base mound
  const rootBaseGeo = new T.CylinderGeometry(0.24, 0.42, 0.45, 9);
  rootBaseGeo.translate(0, 0.22, 0);
  const rootBase = new T.Mesh(rootBaseGeo, barkMat);
  rootBase.castShadow = true;
  rootBase.receiveShadow = true;
  group.add(rootBase);

  // Gnarled surface roots spreading along ground
  const rootSpecs = [
    [new T.Vector3(0.1, 0.2, 0.1), new T.Vector3(0.5, 0.08, 0.4), new T.Vector3(0.85, 0.02, 0.65)],
    [new T.Vector3(-0.1, 0.2, 0.1), new T.Vector3(-0.55, 0.08, 0.35), new T.Vector3(-0.9, 0.02, 0.5)],
    [new T.Vector3(0.05, 0.2, -0.15), new T.Vector3(0.4, 0.08, -0.6), new T.Vector3(0.75, 0.02, -0.9)],
    [new T.Vector3(-0.15, 0.2, -0.1), new T.Vector3(-0.6, 0.09, -0.45), new T.Vector3(-0.95, 0.02, -0.7)],
  ];
  for (const pts of rootSpecs) {
    createBranch(pts, 0.11, 0.03, 6, 6, trunkDetailMat);
  }

  // --- MULTI-STEMMED MAIN TRUNKS & SPREADING GNARLED BRANCHES ---
  // Main Trunk 1: sweeps to the right and forward (+X, +Z)
  createBranch([
    new T.Vector3(0, 0.2, 0),
    new T.Vector3(0.2, 0.7, 0.15),
    new T.Vector3(0.5, 1.3, 0.3),
    new T.Vector3(0.9, 1.8, 0.45),
    new T.Vector3(1.3, 2.3, 0.5)
  ], 0.22, 0.13, 10, 7);

  // Main Trunk 2: sweeps to the left and back (-X, -Z)
  createBranch([
    new T.Vector3(0, 0.2, 0),
    new T.Vector3(-0.25, 0.8, -0.1),
    new T.Vector3(-0.65, 1.5, -0.3),
    new T.Vector3(-1.1, 2.1, -0.45),
    new T.Vector3(-1.4, 2.5, -0.5)
  ], 0.20, 0.12, 10, 7);

  // Main Trunk 3: twists upwards center-left
  createBranch([
    new T.Vector3(0, 0.3, 0),
    new T.Vector3(0.05, 0.9, -0.2),
    new T.Vector3(-0.15, 1.7, -0.25),
    new T.Vector3(0.1, 2.5, 0.0),
    new T.Vector3(0.25, 3.1, 0.1)
  ], 0.18, 0.10, 10, 7);

  // Secondary & Sweeping Horizontal Sub-branches
  const branchConfigs = [
    // From Trunk 1 (Right-Front)
    {
      pts: [new T.Vector3(1.3, 2.3, 0.5), new T.Vector3(1.7, 2.5, 0.7), new T.Vector3(2.0, 2.4, 0.9), new T.Vector3(2.15, 2.3, 1.0)],
      r0: 0.12, r1: 0.04
    },
    {
      pts: [new T.Vector3(1.3, 2.3, 0.5), new T.Vector3(1.6, 2.7, 0.1), new T.Vector3(1.9, 2.8, -0.3), new T.Vector3(2.1, 2.7, -0.5)],
      r0: 0.11, r1: 0.04
    },
    {
      pts: [new T.Vector3(0.9, 1.8, 0.45), new T.Vector3(1.4, 1.9, 0.9), new T.Vector3(1.8, 1.8, 1.2), new T.Vector3(1.95, 1.7, 1.35)],
      r0: 0.10, r1: 0.035
    },

    // From Trunk 2 (Left-Back)
    {
      pts: [new T.Vector3(-1.4, 2.5, -0.5), new T.Vector3(-1.8, 2.7, -0.8), new T.Vector3(-2.1, 2.6, -1.0), new T.Vector3(-2.2, 2.4, -1.1)],
      r0: 0.11, r1: 0.04
    },
    {
      pts: [new T.Vector3(-1.4, 2.5, -0.5), new T.Vector3(-1.7, 2.8, 0.0), new T.Vector3(-2.0, 2.9, 0.4), new T.Vector3(-2.15, 2.8, 0.7)],
      r0: 0.10, r1: 0.04
    },
    {
      pts: [new T.Vector3(-0.65, 1.5, -0.3), new T.Vector3(-1.2, 1.7, -0.8), new T.Vector3(-1.6, 1.6, -1.2), new T.Vector3(-1.8, 1.5, -1.4)],
      r0: 0.09, r1: 0.035
    },

    // From Trunk 3 (Center High & Spreading)
    {
      pts: [new T.Vector3(0.25, 3.1, 0.1), new T.Vector3(0.6, 3.4, 0.4), new T.Vector3(0.9, 3.5, 0.6), new T.Vector3(1.1, 3.3, 0.7)],
      r0: 0.09, r1: 0.035
    },
    {
      pts: [new T.Vector3(0.25, 3.1, 0.1), new T.Vector3(-0.3, 3.4, -0.3), new T.Vector3(-0.7, 3.5, -0.5), new T.Vector3(-1.0, 3.3, -0.6)],
      r0: 0.09, r1: 0.035
    },
    {
      pts: [new T.Vector3(0.25, 3.1, 0.1), new T.Vector3(0.1, 3.6, -0.4), new T.Vector3(0.0, 3.75, -0.7), new T.Vector3(-0.1, 3.65, -0.9)],
      r0: 0.08, r1: 0.03
    },
    {
      pts: [new T.Vector3(0.05, 0.9, -0.2), new T.Vector3(0.3, 1.4, -0.8), new T.Vector3(0.7, 1.6, -1.3), new T.Vector3(0.9, 1.5, -1.6)],
      r0: 0.09, r1: 0.035
    },
    {
      pts: [new T.Vector3(0.5, 1.3, 0.3), new T.Vector3(0.4, 1.7, 0.9), new T.Vector3(0.3, 1.8, 1.4), new T.Vector3(0.2, 1.7, 1.7)],
      r0: 0.08, r1: 0.035
    }
  ];

  for (const b of branchConfigs) {
    createBranch(b.pts, b.r0, b.r1, 8, 6);
  }

  // --- CANOPY CLUSTERS (Layered horizontal tiered pads characteristic of Japanese Cloud / Acer Maples) ---
  // Foliage cluster locations at the branch tips and tiers
  const clusterPositions = [
    // High central crown
    { pos: [0.0, 3.8, -0.2], radius: 0.85, height: 0.45, matIdx: 1 },
    { pos: [0.7, 3.6, 0.45], radius: 0.80, height: 0.40, matIdx: 0 },
    { pos: [-0.6, 3.6, -0.4], radius: 0.80, height: 0.40, matIdx: 2 },
    { pos: [0.0, 3.7, -0.8], radius: 0.75, height: 0.38, matIdx: 3 },
    { pos: [-0.3, 3.5, 0.3], radius: 0.70, height: 0.35, matIdx: 1 },

    // Right tier (Upper & Mid)
    { pos: [1.7, 2.9, -0.4], radius: 0.90, height: 0.42, matIdx: 0 },
    { pos: [2.1, 2.7, -0.6], radius: 0.75, height: 0.38, matIdx: 2 },
    { pos: [1.8, 2.6, 0.6], radius: 0.88, height: 0.42, matIdx: 1 },
    { pos: [2.15, 2.35, 0.95], radius: 0.75, height: 0.35, matIdx: 3 },
    { pos: [1.4, 2.8, 0.2], radius: 0.75, height: 0.38, matIdx: 0 },

    // Left tier (Upper & Mid)
    { pos: [-1.8, 2.9, 0.4], radius: 0.90, height: 0.42, matIdx: 2 },
    { pos: [-2.15, 2.8, 0.7], radius: 0.75, height: 0.36, matIdx: 1 },
    { pos: [-1.8, 2.7, -0.8], radius: 0.88, height: 0.42, matIdx: 0 },
    { pos: [-2.2, 2.4, -1.1], radius: 0.75, height: 0.36, matIdx: 3 },
    { pos: [-1.3, 2.7, -0.2], radius: 0.72, height: 0.36, matIdx: 2 },

    // Lower sweeping tiers
    { pos: [1.8, 1.85, 1.3], radius: 0.80, height: 0.35, matIdx: 1 },
    { pos: [0.3, 1.75, 1.65], radius: 0.75, height: 0.35, matIdx: 3 },
    { pos: [-1.7, 1.6, -1.35], radius: 0.80, height: 0.35, matIdx: 0 },
    { pos: [0.85, 1.55, -1.55], radius: 0.75, height: 0.35, matIdx: 2 },
    { pos: [-0.9, 1.8, 1.1], radius: 0.70, height: 0.35, matIdx: 1 }
  ];

  // Build foliage pads using flattened/dished spheroid clusters and delicate fan discs
  const foliageGroup = new T.Group();
  foliageGroup.name = 'FoliageCanopy';

  for (let i = 0; i < clusterPositions.length; i++) {
    const cp = clusterPositions[i];
    const mat = leafMaterials[cp.matIdx % leafMaterials.length];

    // Main cloud pad: squashed sphere
    const padGeo = new T.SphereGeometry(1.0, 10, 8);
    // Flatten Y to make a tiered horizontal dome
    padGeo.scale(cp.radius, cp.height, cp.radius * (0.85 + 0.3 * rand()));
    const padMesh = new T.Mesh(padGeo, mat);
    padMesh.position.set(cp.pos[0], cp.pos[1], cp.pos[2]);
    padMesh.rotation.y = rand() * Math.PI * 2;
    padMesh.rotation.x = (rand() - 0.5) * 0.15;
    padMesh.rotation.z = (rand() - 0.5) * 0.15;
    padMesh.castShadow = true;
    padMesh.receiveShadow = true;
    foliageGroup.add(padMesh);

    // Sub-cluster layer for organic feathering/depth
    const subPadGeo = new T.SphereGeometry(0.7, 8, 6);
    subPadGeo.scale(cp.radius * 0.7, cp.height * 0.8, cp.radius * 0.7);
    const subMat = leafMaterials[(cp.matIdx + 1) % leafMaterials.length];
    const subMesh = new T.Mesh(subPadGeo, subMat);
    subMesh.position.set(
      cp.pos[0] + (rand() - 0.5) * 0.35 * cp.radius,
      cp.pos[1] + 0.12,
      cp.pos[2] + (rand() - 0.5) * 0.35 * cp.radius
    );
    subMesh.rotation.y = rand() * Math.PI * 2;
    subMesh.castShadow = true;
    subMesh.receiveShadow = true;
    foliageGroup.add(subMesh);
  }

  group.add(foliageGroup);

  // Subtle wind animation for delicate maple foliage
  group.userData.tick = (t, dt) => {
    const sway = Math.sin(t * 1.8) * 0.02 + Math.cos(t * 2.7) * 0.01;
    foliageGroup.rotation.z = sway;
    foliageGroup.rotation.x = Math.sin(t * 1.2) * 0.015;
  };

  group.userData.size = [4.6, 4.2, 4.4];

  return group;
}
