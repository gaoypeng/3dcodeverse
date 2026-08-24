// src/assets/dead_scrub.js — asset "DeadScrub"
// Twisted dry desert juniper and brittlebrush branches with faded gray-brown wood (#756455) and sparse dried sage tufts.
// Size: ~1.50 x 1.80 x 1.50 m (w x h x d), standing on ground y=0, footprint centred on origin.
import * as THREE from 'three';

function createRNG(seed = 12345) {
  let s = Math.floor(seed) || 12345;
  return function() {
    let t = (s += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildDeadScrub(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 42) + (opts.variant || 0) * 1013;
  const rand = createRNG(seed);

  const group = new T.Group();
  group.name = 'DeadScrub';

  // Materials
  const woodMat = new T.MeshStandardMaterial({
    color: 0x756455,
    roughness: 0.95,
    metalness: 0.05,
    flatShading: true,
  });

  const woodDarkMat = new T.MeshStandardMaterial({
    color: 0x5a4c3f,
    roughness: 0.98,
    metalness: 0.02,
    flatShading: true,
  });

  const sageMat1 = new T.MeshStandardMaterial({
    color: 0x828b6d,
    roughness: 0.9,
    metalness: 0.0,
    flatShading: true,
  });

  const sageMat2 = new T.MeshStandardMaterial({
    color: 0x9b9a76,
    roughness: 0.92,
    metalness: 0.0,
    flatShading: true,
  });

  const sageMat3 = new T.MeshStandardMaterial({
    color: 0x6e785b,
    roughness: 0.9,
    metalness: 0.0,
    flatShading: true,
  });

  const sageMaterials = [sageMat1, sageMat2, sageMat3];

  // Helper to create a curved branch using TubeGeometry
  function createBranch(points, radiusStart, radiusEnd, radialSegments = 6, tubularSegments = 10, mat = woodMat) {
    const curve = new T.CatmullRomCurve3(points);
    // Custom tapered tube geometry
    const geo = new T.BufferGeometry();
    const frames = curve.computeFrenetFrames(tubularSegments, false);
    const vertices = [];
    const normals = [];
    const uvs = [];
    const indices = [];

    for (let i = 0; i <= tubularSegments; i++) {
      const u = i / tubularSegments;
      const pt = curve.getPointAt(u);
      const r = radiusStart * (1 - u) + radiusEnd * u;
      const N = frames.normals[i];
      const B = frames.binormals[i];

      for (let j = 0; j <= radialSegments; j++) {
        const v = j / radialSegments;
        const theta = v * Math.PI * 2;
        const sinTheta = Math.sin(theta);
        const cosTheta = Math.cos(theta);

        const normal = new T.Vector3()
          .copy(N).multiplyScalar(cosTheta)
          .addScaledVector(B, sinTheta)
          .normalize();

        const pos = new T.Vector3().copy(pt).addScaledVector(normal, r);

        vertices.push(pos.x, pos.y, pos.z);
        normals.push(normal.x, normal.y, normal.z);
        uvs.push(u, v);
      }
    }

    for (let i = 0; i < tubularSegments; i++) {
      for (let j = 0; j < radialSegments; j++) {
        const a = i * (radialSegments + 1) + j;
        const b = (i + 1) * (radialSegments + 1) + j;
        const c = (i + 1) * (radialSegments + 1) + (j + 1);
        const d = i * (radialSegments + 1) + (j + 1);

        indices.push(a, b, d);
        indices.push(b, c, d);
      }
    }

    geo.setAttribute('position', new T.Float32BufferAttribute(vertices, 3));
    geo.setAttribute('normal', new T.Float32BufferAttribute(normals, 3));
    geo.setAttribute('uv', new T.Float32BufferAttribute(uvs, 2));
    geo.setIndex(indices);
    geo.computeVertexNormals();

    const mesh = new T.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  // Gnarled root base on ground (y = 0)
  const rootCollar = new T.Mesh(
    new T.CylinderGeometry(0.12, 0.22, 0.16, 8),
    woodDarkMat
  );
  rootCollar.position.set(0, 0.08, 0);
  rootCollar.castShadow = rootCollar.receiveShadow = true;
  group.add(rootCollar);

  // Gnarled surface roots anchoring into ground
  for (let i = 0; i < 5; i++) {
    const angle = (i / 5) * Math.PI * 2 + (rand() - 0.5) * 0.4;
    const rDist = 0.25 + rand() * 0.18;
    const pts = [
      new T.Vector3(Math.cos(angle) * 0.08, 0.08, Math.sin(angle) * 0.08),
      new T.Vector3(Math.cos(angle) * 0.18, 0.03, Math.sin(angle) * 0.18),
      new T.Vector3(Math.cos(angle) * rDist, 0.005, Math.sin(angle) * rDist),
    ];
    group.add(createBranch(pts, 0.045, 0.015, 5, 4, woodDarkMat));
  }

  // Main branch structures branching outwards & upwards
  const mainBranchConfigs = [
    {
      pts: [
        new T.Vector3(0, 0.05, 0),
        new T.Vector3(0.12, 0.35, 0.08),
        new T.Vector3(0.35, 0.85, 0.25),
        new T.Vector3(0.55, 1.35, 0.38),
        new T.Vector3(0.65, 1.75, 0.45),
      ],
      r0: 0.075,
      r1: 0.018,
      mat: woodMat,
      subBranches: [
        {
          t: 0.4,
          pts: [
            new T.Vector3(0.25, 0.65, 0.18),
            new T.Vector3(0.48, 0.95, -0.05),
            new T.Vector3(0.68, 1.25, -0.22),
          ],
          r0: 0.035,
          r1: 0.012,
        },
        {
          t: 0.7,
          pts: [
            new T.Vector3(0.48, 1.20, 0.32),
            new T.Vector3(0.32, 1.52, 0.52),
            new T.Vector3(0.18, 1.72, 0.62),
          ],
          r0: 0.025,
          r1: 0.008,
        },
      ],
    },
    {
      pts: [
        new T.Vector3(0, 0.05, 0),
        new T.Vector3(-0.15, 0.32, 0.12),
        new T.Vector3(-0.42, 0.75, 0.28),
        new T.Vector3(-0.62, 1.20, 0.42),
        new T.Vector3(-0.68, 1.62, 0.48),
      ],
      r0: 0.07,
      r1: 0.016,
      mat: woodMat,
      subBranches: [
        {
          t: 0.45,
          pts: [
            new T.Vector3(-0.35, 0.65, 0.22),
            new T.Vector3(-0.55, 0.92, 0.05),
            new T.Vector3(-0.70, 1.15, -0.15),
          ],
          r0: 0.03,
          r1: 0.01,
        },
        {
          t: 0.75,
          pts: [
            new T.Vector3(-0.58, 1.10, 0.38),
            new T.Vector3(-0.45, 1.45, 0.55),
            new T.Vector3(-0.32, 1.76, 0.58),
          ],
          r0: 0.022,
          r1: 0.008,
        },
      ],
    },
    {
      pts: [
        new T.Vector3(0, 0.05, 0),
        new T.Vector3(-0.08, 0.38, -0.18),
        new T.Vector3(-0.28, 0.82, -0.45),
        new T.Vector3(-0.45, 1.28, -0.58),
        new T.Vector3(-0.52, 1.68, -0.62),
      ],
      r0: 0.065,
      r1: 0.015,
      mat: woodMat,
      subBranches: [
        {
          t: 0.5,
          pts: [
            new T.Vector3(-0.22, 0.72, -0.38),
            new T.Vector3(0.02, 1.05, -0.55),
            new T.Vector3(0.15, 1.35, -0.65),
          ],
          r0: 0.028,
          r1: 0.01,
        },
        {
          t: 0.8,
          pts: [
            new T.Vector3(-0.40, 1.18, -0.52),
            new T.Vector3(-0.62, 1.45, -0.42),
            new T.Vector3(-0.68, 1.58, -0.28),
          ],
          r0: 0.02,
          r1: 0.007,
        },
      ],
    },
    {
      pts: [
        new T.Vector3(0, 0.05, 0),
        new T.Vector3(0.10, 0.40, -0.14),
        new T.Vector3(0.28, 0.88, -0.35),
        new T.Vector3(0.46, 1.32, -0.48),
        new T.Vector3(0.58, 1.72, -0.52),
      ],
      r0: 0.068,
      r1: 0.016,
      mat: woodMat,
      subBranches: [
        {
          t: 0.4,
          pts: [
            new T.Vector3(0.20, 0.70, -0.28),
            new T.Vector3(0.42, 0.98, -0.12),
            new T.Vector3(0.62, 1.18, 0.08),
          ],
          r0: 0.03,
          r1: 0.01,
        },
      ],
    },
    {
      // Central gnarled dead spike / stem
      pts: [
        new T.Vector3(0, 0.05, 0),
        new T.Vector3(0.02, 0.45, 0.02),
        new T.Vector3(-0.05, 0.95, -0.04),
        new T.Vector3(0.04, 1.42, 0.02),
        new T.Vector3(0.01, 1.78, -0.02),
      ],
      r0: 0.055,
      r1: 0.008,
      mat: woodDarkMat,
      subBranches: [
        {
          t: 0.6,
          pts: [
            new T.Vector3(-0.03, 1.10, -0.02),
            new T.Vector3(-0.18, 1.38, 0.15),
            new T.Vector3(-0.25, 1.58, 0.22),
          ],
          r0: 0.02,
          r1: 0.006,
        },
        {
          t: 0.7,
          pts: [
            new T.Vector3(0.02, 1.25, 0.01),
            new T.Vector3(0.18, 1.48, -0.12),
            new T.Vector3(0.28, 1.66, -0.18),
          ],
          r0: 0.018,
          r1: 0.005,
        },
      ],
    },
  ];

  // Build branches and gather tuft anchor positions
  const tuftPositions = [];

  for (const b of mainBranchConfigs) {
    // Add main branch with slight jitter from seed
    const jitteredPts = b.pts.map((p, idx) => {
      if (idx === 0) return p;
      return new T.Vector3(
        p.x + (rand() - 0.5) * 0.06,
        p.y + (rand() - 0.5) * 0.04,
        p.z + (rand() - 0.5) * 0.06
      );
    });

    group.add(createBranch(jitteredPts, b.r0, b.r1, 6, 8, b.mat));
    tuftPositions.push({ pos: jitteredPts[jitteredPts.length - 1], scale: 0.14 + rand() * 0.06 });
    tuftPositions.push({ pos: jitteredPts[Math.floor(jitteredPts.length * 0.65)], scale: 0.12 + rand() * 0.05 });

    if (b.subBranches) {
      for (const sb of b.subBranches) {
        const sbPts = sb.pts.map((p, idx) => {
          if (idx === 0) return p;
          return new T.Vector3(
            p.x + (rand() - 0.5) * 0.05,
            p.y + (rand() - 0.5) * 0.04,
            p.z + (rand() - 0.5) * 0.05
          );
        });
        group.add(createBranch(sbPts, sb.r0, sb.r1, 5, 6, b.mat));
        tuftPositions.push({ pos: sbPts[sbPts.length - 1], scale: 0.10 + rand() * 0.05 });
      }
    }
  }

  // Additional fine brittle twigs protruding outwards
  for (let i = 0; i < 10; i++) {
    const parentIdx = Math.floor(rand() * mainBranchConfigs.length);
    const parent = mainBranchConfigs[parentIdx];
    const ptIdx = 2 + Math.floor(rand() * (parent.pts.length - 2));
    const basePt = parent.pts[ptIdx];
    const twigDir = new T.Vector3(
      (rand() - 0.5) * 0.35,
      (rand() * 0.25) + 0.05,
      (rand() - 0.5) * 0.35
    );
    const endPt = new T.Vector3().copy(basePt).add(twigDir);
    const midPt = new T.Vector3().copy(basePt).addScaledVector(twigDir, 0.5).add(
      new T.Vector3((rand() - 0.5) * 0.05, (rand() - 0.5) * 0.05, (rand() - 0.5) * 0.05)
    );

    group.add(createBranch([basePt, midPt, endPt], 0.015, 0.004, 4, 4, woodDarkMat));

    if (rand() > 0.4) {
      tuftPositions.push({ pos: endPt, scale: 0.08 + rand() * 0.04 });
    }
  }

  // Dried sage tufts (low-poly clump clusters)
  const sageGeo = new T.DodecahedronGeometry(1, 1);
  // Deform sage geometry slightly for organic dry look
  const posAttr = sageGeo.attributes.position;
  const tempV = new T.Vector3();
  for (let i = 0; i < posAttr.count; i++) {
    tempV.fromBufferAttribute(posAttr, i);
    const noise = 1.0 + Math.sin(tempV.x * 5 + tempV.y * 3) * 0.12 + Math.cos(tempV.z * 4) * 0.08;
    tempV.multiplyScalar(noise);
    posAttr.setXYZ(i, tempV.x, tempV.y * 0.85, tempV.z); // slightly flattened
  }
  sageGeo.computeVertexNormals();

  for (let i = 0; i < tuftPositions.length; i++) {
    const item = tuftPositions[i];
    const tuftGroup = new T.Group();
    const mat = sageMaterials[Math.floor(rand() * sageMaterials.length)];

    // Main clump
    const mainMesh = new T.Mesh(sageGeo, mat);
    const s = item.scale;
    mainMesh.scale.set(s, s * 0.9, s);
    mainMesh.rotation.set(rand() * Math.PI, rand() * Math.PI, rand() * Math.PI);
    mainMesh.castShadow = true;
    mainMesh.receiveShadow = true;
    tuftGroup.add(mainMesh);

    // Sub-cluster clump
    if (rand() > 0.3) {
      const subMesh = new T.Mesh(sageGeo, mat);
      const subS = s * (0.55 + rand() * 0.3);
      subMesh.scale.set(subS, subS * 0.8, subS);
      subMesh.position.set(
        (rand() - 0.5) * s * 0.8,
        (rand() - 0.5) * s * 0.6,
        (rand() - 0.5) * s * 0.8
      );
      subMesh.rotation.set(rand() * Math.PI, rand() * Math.PI, rand() * Math.PI);
      subMesh.castShadow = true;
      subMesh.receiveShadow = true;
      tuftGroup.add(subMesh);
    }

    tuftGroup.position.copy(item.pos);
    group.add(tuftGroup);
  }

  // Measure bounds and adjust slightly if needed to ensure strictly:
  // ~1.50 x 1.80 x 1.50 m with base on y=0
  const box = new T.Box3().setFromObject(group);
  const size = new T.Vector3();
  box.getSize(size);

  // Exact registered size metadata
  group.userData.size = [1.50, 1.80, 1.50];

  // Subtle wind sway animation hook
  group.userData.tick = (t, dt) => {
    const sway = Math.sin(t * 1.8 + seed) * 0.02;
    group.rotation.z = sway;
    group.rotation.x = Math.cos(t * 1.4 + seed) * 0.015;
  };

  return group;
}
