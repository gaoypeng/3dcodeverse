// src/assets/bamboo_fence.js — Takegaki bamboo screen section
// Vertical cured bamboo poles tied with dark hemp cord to horizontal bamboo rails,
// supported by sturdy end posts and top coping rail / cap.
// Target size: ~ 2.20 (W) x 1.80 (H) x 0.15 (D) meters. Y-up, base at y=0, centred on X/Z.
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildBambooFence(THREE_LIB = THREE, opts = {}) {
  const T = THREE_LIB || THREE;
  const group = new T.Group();
  group.name = 'BambooFence';

  const seed = opts.seed !== undefined ? opts.seed : 42;
  const rand = mulberry32(seed);

  // Materials
  const postWoodMat = new T.MeshStandardMaterial({
    color: 0x4a3728, // Dark aged cedar post
    roughness: 0.85,
    metalness: 0.05
  });

  const bambooPoleMat = new T.MeshStandardMaterial({
    color: 0xc8b27a, // Cured golden/straw bamboo
    roughness: 0.55,
    metalness: 0.05
  });

  const bambooPoleAltMat = new T.MeshStandardMaterial({
    color: 0xbfa468, // Slight variation for organic look
    roughness: 0.58,
    metalness: 0.05
  });

  const bambooNodeMat = new T.MeshStandardMaterial({
    color: 0x8a7042, // Bamboo node ring color
    roughness: 0.7,
    metalness: 0.05
  });

  const cordMat = new T.MeshStandardMaterial({
    color: 0x222220, // Dark blackened hemp / shuro-nawa cord
    roughness: 0.95,
    metalness: 0.0
  });

  const topRailMat = new T.MeshStandardMaterial({
    color: 0x9b8555, // Weathered bamboo top cap
    roughness: 0.65,
    metalness: 0.05
  });

  // Dimensions
  const totalWidth = 2.20;
  const totalHeight = 1.80;
  const totalDepth = 0.15;

  const postRadius = 0.045; // 9cm diameter main posts
  const postHeight = totalHeight; // 1.80m
  const postX = (totalWidth / 2) - postRadius; // +/- 1.055

  // 1. Two Main End Posts (Heavy cedar/bamboo round posts)
  const postGeo = new T.CylinderGeometry(postRadius, postRadius * 1.05, postHeight, 16);
  
  const leftPost = new T.Mesh(postGeo, postWoodMat);
  leftPost.position.set(-postX, postHeight / 2, 0);
  leftPost.castShadow = true;
  leftPost.receiveShadow = true;
  group.add(leftPost);

  const rightPost = new T.Mesh(postGeo, postWoodMat);
  rightPost.position.set(postX, postHeight / 2, 0);
  rightPost.castShadow = true;
  rightPost.receiveShadow = true;
  group.add(rightPost);

  // Post top caps (chamfered bevel / slight conical peak)
  const postCapGeo = new T.ConeGeometry(postRadius * 1.08, 0.04, 16);
  const leftCap = new T.Mesh(postCapGeo, postWoodMat);
  leftCap.position.set(-postX, postHeight + 0.018, 0);
  leftCap.castShadow = true;
  group.add(leftCap);

  const rightCap = new T.Mesh(postCapGeo, postWoodMat);
  rightCap.position.set(postX, postHeight + 0.018, 0);
  rightCap.castShadow = true;
  group.add(rightCap);

  // 2. Horizontal Rails (3 tiers: lower, middle, upper)
  const railLength = totalWidth - (postRadius * 1.5);
  const railRadius = 0.024;
  const railHeights = [0.28, 0.90, 1.55];
  
  // Create front and back horizontal rail pairs (or staggered through posts)
  // Traditional Takegaki has horizontal backing rails and a front cross brace
  const railGeo = new T.CylinderGeometry(railRadius, railRadius, railLength, 12);
  railGeo.rotateZ(Math.PI / 2);

  railHeights.forEach((ry, idx) => {
    // Front rail
    const railFront = new T.Mesh(railGeo, topRailMat);
    railFront.position.set(0, ry, 0.028);
    railFront.castShadow = true;
    railFront.receiveShadow = true;
    group.add(railFront);

    // Back rail
    const railBack = new T.Mesh(railGeo, topRailMat);
    railBack.position.set(0, ry, -0.028);
    railBack.castShadow = true;
    railBack.receiveShadow = true;
    group.add(railBack);
  });

  // 3. Top Coping / Bamboo split roof cap (Kasagi)
  const topCopingGeo = new T.CylinderGeometry(0.042, 0.042, totalWidth + 0.06, 16, 1, false, 0, Math.PI);
  // Rotate so the arch curves downwards over the fence top
  topCopingGeo.rotateZ(Math.PI / 2);
  topCopingGeo.rotateX(-Math.PI / 2);
  const topCoping = new T.Mesh(topCopingGeo, topRailMat);
  topCoping.position.set(0, 1.76, 0);
  topCoping.castShadow = true;
  topCoping.receiveShadow = true;
  group.add(topCoping);

  // 4. Vertical Bamboo Pickets / Slats (Dense screen)
  const innerWidth = (postX - postRadius) * 2;
  const numPickets = 42;
  const slatSpacing = innerWidth / (numPickets + 1);
  const slatRadius = 0.016;
  const slatHeight = 1.72;

  // Single picket geometry with node rings built in or instanced
  const picketGeo = new T.CylinderGeometry(slatRadius, slatRadius * 0.96, slatHeight, 10);
  const nodeRingGeo = new T.TorusGeometry(slatRadius * 1.05, 0.0035, 6, 10);
  nodeRingGeo.rotateX(Math.PI / 2);

  const picketMesh1 = new T.InstancedMesh(picketGeo, bambooPoleMat, Math.ceil(numPickets / 2));
  const picketMesh2 = new T.InstancedMesh(picketGeo, bambooPoleAltMat, Math.floor(numPickets / 2));
  picketMesh1.castShadow = picketMesh1.receiveShadow = true;
  picketMesh2.castShadow = picketMesh2.receiveShadow = true;

  // Store node ring instances: ~4 nodes per picket
  const totalNodes = numPickets * 4;
  const nodeMesh = new T.InstancedMesh(nodeRingGeo, bambooNodeMat, totalNodes);
  nodeMesh.castShadow = true;

  const m4 = new T.Matrix4();
  const q = new T.Quaternion();
  const scale = new T.Vector3(1, 1, 1);
  const pos = new T.Vector3();

  let count1 = 0;
  let count2 = 0;
  let nodeCount = 0;

  for (let i = 0; i < numPickets; i++) {
    const x = -innerWidth / 2 + (i + 1) * slatSpacing + (rand() - 0.5) * 0.003;
    // slight natural stagger in Z
    const z = (rand() - 0.5) * 0.006;
    const hScale = 0.97 + rand() * 0.05;
    const currentH = slatHeight * hScale;
    const y = currentH / 2 + 0.01;

    pos.set(x, y, z);
    // slight tilt
    q.setFromEuler(new T.Euler((rand() - 0.5) * 0.015, 0, (rand() - 0.5) * 0.015));
    scale.set(1, hScale, 1);
    m4.compose(pos, q, scale);

    if (i % 2 === 0) {
      picketMesh1.setMatrixAt(count1++, m4);
    } else {
      picketMesh2.setMatrixAt(count2++, m4);
    }

    // Add 4 bamboo node rings along the vertical slat
    const baseNodeY = 0.2 + rand() * 0.1;
    for (let n = 0; n < 4; n++) {
      const ny = baseNodeY + n * (0.35 + rand() * 0.06);
      if (ny < currentH - 0.05) {
        pos.set(x, ny, z);
        scale.set(1 + (rand() - 0.5) * 0.1, 1 + (rand() - 0.5) * 0.1, 1);
        m4.compose(pos, q, scale);
        nodeMesh.setMatrixAt(nodeCount++, m4);
      }
    }
  }

  picketMesh1.instanceMatrix.needsUpdate = true;
  picketMesh2.instanceMatrix.needsUpdate = true;
  nodeMesh.instanceMatrix.needsUpdate = true;

  group.add(picketMesh1);
  group.add(picketMesh2);
  group.add(nodeMesh);

  // 5. Hemp Cord Ties (Otokomusubi knots & cross wraps at rail intersections)
  // We place cross ties at selected pickets along each horizontal rail level
  const cordKnotGeo = new T.TorusGeometry(0.026, 0.006, 6, 8);
  const cordCrossGeo = new T.BoxGeometry(0.045, 0.045, 0.07);
  const tiesPerRail = 14;
  const totalTies = tiesPerRail * railHeights.length;
  
  const cordMesh = new T.InstancedMesh(cordCrossGeo, cordMat, totalTies * 2);
  cordMesh.castShadow = true;
  let cordCount = 0;

  railHeights.forEach((ry) => {
    for (let k = 0; k < tiesPerRail; k++) {
      const idx = Math.floor(k * (numPickets / tiesPerRail)) + 1;
      const x = -innerWidth / 2 + idx * slatSpacing;
      
      // X-shaped cord wrap simulation
      pos.set(x, ry, 0);
      q.setFromEuler(new T.Euler(0, 0, Math.PI / 4));
      scale.set(0.2, 1, 0.85);
      m4.compose(pos, q, scale);
      cordMesh.setMatrixAt(cordCount++, m4);

      q.setFromEuler(new T.Euler(0, 0, -Math.PI / 4));
      m4.compose(pos, q, scale);
      cordMesh.setMatrixAt(cordCount++, m4);
    }
  });

  // End-post heavy decorative knots at each rail joint
  const postKnotGeo = new T.TorusGeometry(postRadius * 1.15, 0.012, 8, 16);
  postKnotGeo.rotateX(Math.PI / 2);
  const endPostCordMesh = new T.InstancedMesh(postKnotGeo, cordMat, railHeights.length * 4);
  endPostCordMesh.castShadow = true;
  let endCordCount = 0;

  railHeights.forEach(ry => {
    [-postX, postX].forEach(px => {
      [-0.02, 0.02].forEach(dy => {
        pos.set(px, ry + dy, 0);
        q.identity();
        scale.set(1, 1, 1);
        m4.compose(pos, q, scale);
        endPostCordMesh.setMatrixAt(endCordCount++, m4);
      });
    });
  });

  cordMesh.instanceMatrix.needsUpdate = true;
  endPostCordMesh.instanceMatrix.needsUpdate = true;
  group.add(cordMesh);
  group.add(endPostCordMesh);

  // Set metadata
  group.userData.size = [totalWidth, totalHeight, totalDepth];

  return group;
}
