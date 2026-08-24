// src/assets/potted_tree.js — asset "PottedTree": Textured terracotta pot hosting a slender potted olive tree with gnarled trunk and silvery-green foliage.
// approx size 0.90 x 2.40 x 0.90 m (w x h x d)
// CONTRACT: export function buildPottedTree(THREE, opts = {}) → THREE.Group
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildPottedTree(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 42) + (opts.variant || 0) * 1013;
  const rng = mulberry32(seed);

  const group = new T.Group();
  group.name = 'PottedTree';

  // --- Materials ---
  const terracottaMat = new T.MeshStandardMaterial({
    color: 0xbf633f,
    roughness: 0.84,
    metalness: 0.05,
  });

  const potBandMat = new T.MeshStandardMaterial({
    color: 0xb05533,
    roughness: 0.88,
    metalness: 0.05,
  });

  const soilMat = new T.MeshStandardMaterial({
    color: 0x2b211a,
    roughness: 0.95,
    metalness: 0.0,
  });

  const barkMat = new T.MeshStandardMaterial({
    color: 0x5e564b,
    roughness: 0.92,
    metalness: 0.04,
  });

  const foliageMat1 = new T.MeshStandardMaterial({
    color: 0x6c7f5f, // silvery olive
    roughness: 0.72,
    metalness: 0.05,
  });

  const foliageMat2 = new T.MeshStandardMaterial({
    color: 0x859676, // pale sage highlight
    roughness: 0.68,
    metalness: 0.05,
  });

  const foliageMat3 = new T.MeshStandardMaterial({
    color: 0x546648, // deeper olive shade
    roughness: 0.75,
    metalness: 0.05,
  });

  const oliveFruitMat = new T.MeshStandardMaterial({
    color: 0x222619, // dark ripe olive
    roughness: 0.35,
    metalness: 0.1,
  });

  // ==========================================
  // 1. Terracotta Pot
  // Base at y = 0, Pot height = 0.52m
  // ==========================================
  const potGroup = new T.Group();
  potGroup.name = 'TerracottaPot';

  // Pot base foot
  const footGeo = new T.CylinderGeometry(0.19, 0.20, 0.03, 24);
  const foot = new T.Mesh(footGeo, terracottaMat);
  foot.position.y = 0.015;
  foot.castShadow = foot.receiveShadow = true;
  potGroup.add(foot);

  // Pot main body (tapered cylinder: bottom r=0.20, top r=0.255, height=0.46)
  const bodyGeo = new T.CylinderGeometry(0.255, 0.20, 0.46, 24);
  const body = new T.Mesh(bodyGeo, terracottaMat);
  body.position.y = 0.26;
  body.castShadow = body.receiveShadow = true;
  potGroup.add(body);

  // Decorative raised relief bands on pot exterior
  const bandGeo1 = new T.TorusGeometry(0.232, 0.008, 8, 24);
  bandGeo1.rotateX(Math.PI / 2);
  const band1 = new T.Mesh(bandGeo1, potBandMat);
  band1.position.y = 0.34;
  band1.castShadow = true;
  potGroup.add(band1);

  const bandGeo2 = new T.TorusGeometry(0.244, 0.008, 8, 24);
  bandGeo2.rotateX(Math.PI / 2);
  const band2 = new T.Mesh(bandGeo2, potBandMat);
  band2.position.y = 0.41;
  band2.castShadow = true;
  potGroup.add(band2);

  // Pot rim / rolled lip
  const rimGeo = new T.TorusGeometry(0.26, 0.018, 12, 28);
  rimGeo.rotateX(Math.PI / 2);
  const rim = new T.Mesh(rimGeo, terracottaMat);
  rim.position.y = 0.49;
  rim.castShadow = rim.receiveShadow = true;
  potGroup.add(rim);

  const rimInnerGeo = new T.CylinderGeometry(0.262, 0.252, 0.04, 24);
  const rimInner = new T.Mesh(rimInnerGeo, terracottaMat);
  rimInner.position.y = 0.49;
  rimInner.castShadow = rimInner.receiveShadow = true;
  potGroup.add(rimInner);

  // Soil inside pot (y = 0.485)
  const soilGeo = new T.CylinderGeometry(0.245, 0.235, 0.04, 20);
  const soil = new T.Mesh(soilGeo, soilMat);
  soil.position.y = 0.48;
  soil.receiveShadow = true;
  potGroup.add(soil);

  group.add(potGroup);

  // ==========================================
  // 2. Olive Tree Trunk & Branches (gnarled, organic)
  // ==========================================
  const treeGroup = new T.Group();
  treeGroup.name = 'OliveTree';

  // Root flare / trunk base
  const baseFlangeGeo = new T.CylinderGeometry(0.045, 0.065, 0.08, 12);
  const baseFlange = new T.Mesh(baseFlangeGeo, barkMat);
  baseFlange.position.y = 0.51;
  baseFlange.castShadow = baseFlange.receiveShadow = true;
  treeGroup.add(baseFlange);

  // Helper to build organic curved branch tubes using CatmullRomCurve3
  function createBranch(points, radiusStart, radiusEnd, segments = 12, radialSegments = 8) {
    const curve = new T.CatmullRomCurve3(points);
    const frames = curve.computeFrenetFrames(segments, false);
    const geometry = new T.BufferGeometry();
    const positions = [];
    const normals = [];
    const uvs = [];
    const indices = [];

    for (let i = 0; i <= segments; i++) {
      const u = i / segments;
      const pt = curve.getPointAt(u);
      const N = frames.normals[i];
      const B = frames.binormals[i];
      const r = radiusStart * (1 - u) + radiusEnd * u;

      for (let j = 0; j <= radialSegments; j++) {
        const v = j / radialSegments;
        const angle = v * Math.PI * 2;
        const sin = Math.sin(angle);
        const cos = -Math.cos(angle);

        // Add subtle gnarl noise
        const gnarl = (rng() - 0.5) * 0.003;
        const normalX = cos * N.x + sin * B.x;
        const normalY = cos * N.y + sin * B.y;
        const normalZ = cos * N.z + sin * B.z;

        const posX = pt.x + normalX * (r + gnarl);
        const posY = pt.y + normalY * (r + gnarl);
        const posZ = pt.z + normalZ * (r + gnarl);

        positions.push(posX, posY, posZ);
        normals.push(normalX, normalY, normalZ);
        uvs.push(u, v);
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

    geometry.setAttribute('position', new T.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new T.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('uv', new T.Float32BufferAttribute(uvs, 2));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();

    const mesh = new T.Mesh(geometry, barkMat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  // Main trunk with gnarled twists
  const mainTrunkPts = [
    new T.Vector3(0.0, 0.49, 0.0),
    new T.Vector3(0.02, 0.70, 0.015),
    new T.Vector3(-0.025, 0.95, -0.02),
    new T.Vector3(0.01, 1.20, 0.01),
    new T.Vector3(0.03, 1.45, -0.02),
    new T.Vector3(0.0, 1.70, 0.0),
  ];
  treeGroup.add(createBranch(mainTrunkPts, 0.048, 0.030, 14, 8));

  // Branch 1: Reaches +X, +Z
  const b1Pts = [
    new T.Vector3(0.01, 1.22, 0.01),
    new T.Vector3(0.12, 1.38, 0.08),
    new T.Vector3(0.24, 1.55, 0.16),
    new T.Vector3(0.32, 1.75, 0.20),
    new T.Vector3(0.36, 1.92, 0.18),
  ];
  treeGroup.add(createBranch(b1Pts, 0.025, 0.010, 10, 6));

  // Branch 2: Reaches -X, +Z
  const b2Pts = [
    new T.Vector3(-0.02, 1.30, -0.01),
    new T.Vector3(-0.14, 1.48, 0.09),
    new T.Vector3(-0.25, 1.68, 0.15),
    new T.Vector3(-0.33, 1.88, 0.12),
    new T.Vector3(-0.35, 2.02, 0.05),
  ];
  treeGroup.add(createBranch(b2Pts, 0.024, 0.009, 10, 6));

  // Branch 3: Reaches -Z
  const b3Pts = [
    new T.Vector3(0.02, 1.40, -0.02),
    new T.Vector3(0.05, 1.58, -0.15),
    new T.Vector3(-0.06, 1.76, -0.26),
    new T.Vector3(-0.08, 1.95, -0.32),
    new T.Vector3(-0.02, 2.10, -0.30),
  ];
  treeGroup.add(createBranch(b3Pts, 0.022, 0.009, 10, 6));

  // Branch 4: Central / Upper crown leader reaching +Y and slight +X, -Z
  const b4Pts = [
    new T.Vector3(0.0, 1.68, 0.0),
    new T.Vector3(0.04, 1.88, -0.04),
    new T.Vector3(0.02, 2.08, 0.02),
    new T.Vector3(0.0, 2.25, 0.0),
  ];
  treeGroup.add(createBranch(b4Pts, 0.020, 0.008, 8, 6));

  // Branch 5: Lower right side accent branch
  const b5Pts = [
    new T.Vector3(0.18, 1.46, 0.12),
    new T.Vector3(0.28, 1.55, -0.05),
    new T.Vector3(0.35, 1.70, -0.14),
  ];
  treeGroup.add(createBranch(b5Pts, 0.016, 0.008, 6, 6));

  // Branch 6: Left high fork
  const b6Pts = [
    new T.Vector3(-0.18, 1.58, 0.12),
    new T.Vector3(-0.26, 1.78, -0.10),
    new T.Vector3(-0.30, 1.95, -0.18),
  ];
  treeGroup.add(createBranch(b6Pts, 0.015, 0.008, 6, 6));

  // ==========================================
  // 3. Silvery-Green Foliage Clouds & Olives
  // ==========================================
  const canopyGroup = new T.Group();
  canopyGroup.name = 'OliveCanopy';

  // Base geometry for leaf clusters: soft squished icosahedron
  const foliageGeos = [
    new T.IcosahedronGeometry(0.18, 1),
    new T.IcosahedronGeometry(0.22, 1),
    new T.IcosahedronGeometry(0.15, 1),
  ];

  // Deform the foliage cluster geometries slightly for leafy organic silhouettes
  foliageGeos.forEach((geo) => {
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const vx = pos.getX(i);
      const vy = pos.getY(i);
      const vz = pos.getZ(i);
      pos.setXYZ(
        i,
        vx * (1.0 + (rng() - 0.5) * 0.25),
        vy * (0.75 + (rng() - 0.5) * 0.2),
        vz * (1.0 + (rng() - 0.5) * 0.25)
      );
    }
    geo.computeVertexNormals();
  });

  const clusterConfigs = [
    // Top crown (reaches y ~ 2.38m)
    { x: 0.00, y: 2.26, z: 0.00, sx: 1.1, sy: 0.9, sz: 1.1, mat: foliageMat2, geoIdx: 1 },
    { x: 0.06, y: 2.30, z: -0.05, sx: 0.85, sy: 0.8, sz: 0.85, mat: foliageMat1, geoIdx: 0 },
    { x: -0.05, y: 2.22, z: 0.06, sx: 0.9, sy: 0.85, sz: 0.9, mat: foliageMat3, geoIdx: 2 },

    // Branch 1 clusters (+X, +Z, width extends to ~0.42m)
    { x: 0.35, y: 1.92, z: 0.18, sx: 1.05, sy: 0.9, sz: 1.0, mat: foliageMat1, geoIdx: 1 },
    { x: 0.26, y: 1.78, z: 0.24, sx: 0.95, sy: 0.85, sz: 0.9, mat: foliageMat2, geoIdx: 0 },
    { x: 0.38, y: 2.05, z: 0.12, sx: 0.8, sy: 0.8, sz: 0.8, mat: foliageMat3, geoIdx: 2 },

    // Branch 2 clusters (-X, +Z, width extends to ~-0.42m)
    { x: -0.34, y: 2.02, z: 0.06, sx: 1.1, sy: 0.95, sz: 1.0, mat: foliageMat1, geoIdx: 1 },
    { x: -0.25, y: 1.86, z: 0.20, sx: 0.9, sy: 0.85, sz: 0.95, mat: foliageMat2, geoIdx: 0 },
    { x: -0.36, y: 2.12, z: -0.04, sx: 0.85, sy: 0.8, sz: 0.85, mat: foliageMat3, geoIdx: 2 },

    // Branch 3 clusters (-Z, depth extends to ~-0.42m)
    { x: -0.02, y: 2.08, z: -0.30, sx: 1.05, sy: 0.9, sz: 1.05, mat: foliageMat2, geoIdx: 1 },
    { x: -0.10, y: 1.94, z: -0.32, sx: 0.9, sy: 0.85, sz: 0.9, mat: foliageMat1, geoIdx: 0 },
    { x: 0.08, y: 1.98, z: -0.24, sx: 0.85, sy: 0.8, sz: 0.85, mat: foliageMat3, geoIdx: 2 },

    // Branch 5 clusters (+X, -Z)
    { x: 0.34, y: 1.72, z: -0.15, sx: 0.95, sy: 0.85, sz: 0.95, mat: foliageMat1, geoIdx: 0 },
    { x: 0.24, y: 1.62, z: -0.18, sx: 0.85, sy: 0.8, sz: 0.85, mat: foliageMat3, geoIdx: 2 },

    // Branch 6 clusters (-X, -Z)
    { x: -0.28, y: 1.95, z: -0.18, sx: 0.95, sy: 0.9, sz: 0.9, mat: foliageMat2, geoIdx: 0 },
    { x: -0.20, y: 1.78, z: -0.15, sx: 0.85, sy: 0.8, sz: 0.85, mat: foliageMat1, geoIdx: 2 },

    // Mid-canopy filling clusters
    { x: 0.12, y: 1.88, z: 0.08, sx: 0.95, sy: 0.9, sz: 0.95, mat: foliageMat1, geoIdx: 0 },
    { x: -0.12, y: 1.92, z: 0.04, sx: 1.0, sy: 0.9, sz: 1.0, mat: foliageMat3, geoIdx: 1 },
    { x: 0.02, y: 1.75, z: -0.08, sx: 0.9, sy: 0.8, sz: 0.9, mat: foliageMat2, geoIdx: 2 },
  ];

  const foliageMeshes = [];

  clusterConfigs.forEach((cfg) => {
    const geo = foliageGeos[cfg.geoIdx];
    const mesh = new T.Mesh(geo, cfg.mat);
    mesh.position.set(cfg.x, cfg.y, cfg.z);
    mesh.scale.set(cfg.sx, cfg.sy, cfg.sz);
    mesh.rotation.set(
      rng() * Math.PI,
      rng() * Math.PI,
      rng() * Math.PI
    );
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    canopyGroup.add(mesh);

    // Save initial state for wind sway animation
    foliageMeshes.push({
      mesh,
      origPos: new T.Vector3(cfg.x, cfg.y, cfg.z),
      origRot: mesh.rotation.clone(),
      phase: rng() * Math.PI * 2,
      freq: 1.5 + rng() * 0.8,
      amp: 0.015 + (cfg.y / 2.4) * 0.02,
    });
  });

  // Hanging ripe olives (subtle Mediterranean touch)
  const oliveGeo = new T.SphereGeometry(0.014, 8, 8);
  oliveGeo.scale(1.0, 1.35, 1.0);

  const olivePositions = [
    [0.30, 1.68, 0.18],
    [0.34, 1.66, 0.14],
    [-0.28, 1.76, 0.15],
    [-0.24, 1.72, 0.19],
    [-0.04, 1.82, -0.28],
    [0.02, 1.78, -0.25],
    [0.28, 1.52, -0.12],
    [-0.22, 1.68, -0.14],
    [0.15, 1.70, 0.12],
  ];

  olivePositions.forEach(([ox, oy, oz]) => {
    const olive = new T.Mesh(oliveGeo, oliveFruitMat);
    olive.position.set(
      ox + (rng() - 0.5) * 0.02,
      oy + (rng() - 0.5) * 0.02,
      oz + (rng() - 0.5) * 0.02
    );
    olive.rotation.set(
      (rng() - 0.5) * 0.4,
      rng() * Math.PI * 2,
      (rng() - 0.5) * 0.4
    );
    olive.castShadow = true;
    canopyGroup.add(olive);
  });

  treeGroup.add(canopyGroup);
  group.add(treeGroup);

  // Overall bounds declaration (0.90 x 2.40 x 0.90 m)
  group.userData.size = [0.90, 2.40, 0.90];

  // Also hook into planter_perimeter / scene animation
  group.userData.tick = (t) => {
    // Gentle trunk/canopy collective sway
    const wind = Math.sin(t * 1.6) * 0.015 + Math.sin(t * 2.7) * 0.008;
    treeGroup.rotation.z = wind * 0.4;
    treeGroup.rotation.x = Math.cos(t * 1.4) * 0.008;

    // Subtle individual leaf cluster flutter
    for (let i = 0; i < foliageMeshes.length; i++) {
      const f = foliageMeshes[i];
      const sway = Math.sin(t * f.freq + f.phase) * f.amp;
      f.mesh.position.x = f.origPos.x + sway;
      f.mesh.position.z = f.origPos.z + Math.cos(t * f.freq * 0.8 + f.phase) * (f.amp * 0.6);
      f.mesh.rotation.z = f.origRot.z + sway * 0.8;
      f.mesh.rotation.y = f.origRot.y + sway * 0.5;
    }
  };

  return group;
}
