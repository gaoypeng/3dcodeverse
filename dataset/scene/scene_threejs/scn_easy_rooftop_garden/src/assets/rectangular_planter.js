import * as THREE from 'three';

// Deterministic PRNG
function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildRectangularPlanter(T = THREE, opts = {}) {
  const seed = opts.seed !== undefined ? opts.seed : 42;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'RectangularPlanter';

  // Dimensions
  const W = 1.80; // width (X)
  const D = 0.55; // depth (Z)
  const planterHeight = 0.45; // trough height (Y)
  const wallThick = 0.03;
  const bottomThick = 0.03;
  const soilInset = 0.05; // soil surface below top edge

  // Materials
  const steelMat = new T.MeshStandardMaterial({
    color: 0x24272a, // Charcoal powder-coated steel
    roughness: 0.55,
    metalness: 0.45,
  });

  const soilMat = new T.MeshStandardMaterial({
    color: 0x2b221a, // Dark garden soil / mulch
    roughness: 0.95,
    metalness: 0.05,
  });

  const boxwoodLeafMat = new T.MeshStandardMaterial({
    color: 0x2e5c1e, // Deep lush boxwood green
    roughness: 0.75,
    metalness: 0.1,
  });

  const boxwoodLeafLightMat = new T.MeshStandardMaterial({
    color: 0x467828, // Fresh tip highlights
    roughness: 0.7,
    metalness: 0.1,
  });

  const pampasStemMat = new T.MeshStandardMaterial({
    color: 0x6e8a4a, // Grass green stem
    roughness: 0.6,
  });

  const pampasPlumeMat = new T.MeshStandardMaterial({
    color: 0xdfd3be, // Warm cream / wheat pampas plumes
    roughness: 0.9,
    metalness: 0.05,
  });

  const lavenderFoliageMat = new T.MeshStandardMaterial({
    color: 0x4d6350, // Grey-green foliage
    roughness: 0.8,
  });

  const lavenderFlowerMat = new T.MeshStandardMaterial({
    color: 0x765898, // Vibrant purple-violet blooms
    roughness: 0.7,
  });

  // --- 1. Charcoal Steel Trough Planter ---
  const planterBody = new T.Group();
  planterBody.name = 'Trough';

  // Bottom base plate
  const bottomMesh = new T.Mesh(
    new T.BoxGeometry(W, bottomThick, D),
    steelMat
  );
  bottomMesh.position.y = bottomThick / 2;
  bottomMesh.castShadow = true;
  bottomMesh.receiveShadow = true;
  planterBody.add(bottomMesh);

  // Front & Back walls (along X)
  const fbWallGeo = new T.BoxGeometry(W, planterHeight - bottomThick, wallThick);
  const frontWall = new T.Mesh(fbWallGeo, steelMat);
  frontWall.position.set(0, bottomThick + (planterHeight - bottomThick) / 2, (D - wallThick) / 2);
  frontWall.castShadow = true;
  frontWall.receiveShadow = true;
  planterBody.add(frontWall);

  const backWall = new T.Mesh(fbWallGeo, steelMat);
  backWall.position.set(0, bottomThick + (planterHeight - bottomThick) / 2, -(D - wallThick) / 2);
  backWall.castShadow = true;
  backWall.receiveShadow = true;
  planterBody.add(backWall);

  // Left & Right walls (along Z)
  const lrWallGeo = new T.BoxGeometry(wallThick, planterHeight - bottomThick, D - wallThick * 2);
  const leftWall = new T.Mesh(lrWallGeo, steelMat);
  leftWall.position.set(-(W - wallThick) / 2, bottomThick + (planterHeight - bottomThick) / 2, 0);
  leftWall.castShadow = true;
  leftWall.receiveShadow = true;
  planterBody.add(leftWall);

  const rightWall = new T.Mesh(lrWallGeo, steelMat);
  rightWall.position.set((W - wallThick) / 2, bottomThick + (planterHeight - bottomThick) / 2, 0);
  rightWall.castShadow = true;
  rightWall.receiveShadow = true;
  planterBody.add(rightWall);

  // Top lip / rim for refined architectural steel finish
  const rimThick = 0.015;
  const rimWidth = 0.04;
  const rimFrontBackGeo = new T.BoxGeometry(W + 0.01, rimThick, rimWidth);
  const rimLRGeo = new T.BoxGeometry(rimWidth, rimThick, D + 0.01);
  const rimFB1 = new T.Mesh(rimFrontBackGeo, steelMat);
  rimFB1.position.set(0, planterHeight + rimThick / 2, (D - rimWidth + 0.01) / 2);
  const rimFB2 = new T.Mesh(rimFrontBackGeo, steelMat);
  rimFB2.position.set(0, planterHeight + rimThick / 2, -(D - rimWidth + 0.01) / 2);
  const rimLR1 = new T.Mesh(rimLRGeo, steelMat);
  rimLR1.position.set(-(W - rimWidth + 0.01) / 2, planterHeight + rimThick / 2, 0);
  const rimLR2 = new T.Mesh(rimLRGeo, steelMat);
  rimLR2.position.set((W - rimWidth + 0.01) / 2, planterHeight + rimThick / 2, 0);
  planterBody.add(rimFB1, rimFB2, rimLR1, rimLR2);

  // Four subtle square feet lifting planter slightly for drainage look, sitting flush at y=0
  const footGeo = new T.BoxGeometry(0.06, 0.02, 0.06);
  const footOffsets = [
    [-W / 2 + 0.08, -D / 2 + 0.08],
    [W / 2 - 0.08, -D / 2 + 0.08],
    [-W / 2 + 0.08, D / 2 - 0.08],
    [W / 2 - 0.08, D / 2 - 0.08],
  ];
  for (const [fx, fz] of footOffsets) {
    const foot = new T.Mesh(footGeo, steelMat);
    foot.position.set(fx, 0.01, fz);
    foot.castShadow = true;
    planterBody.add(foot);
  }

  group.add(planterBody);

  // --- 2. Soil Bed ---
  const soilY = planterHeight - soilInset;
  const soilGeo = new T.BoxGeometry(W - wallThick * 2, 0.06, D - wallThick * 2);
  const soilMesh = new T.Mesh(soilGeo, soilMat);
  soilMesh.position.set(0, soilY, 0);
  soilMesh.receiveShadow = true;
  group.add(soilMesh);

  // --- 3. Foliage & Plants ---
  const foliageGroup = new T.Group();
  foliageGroup.name = 'Foliage';

  // Arrays to hold animated plant parts for tick/sway
  const swayingElements = [];

  // A. Boxwood Shrubs (Rounded dense mounds in middle/front)
  const boxwoodCenters = [
    { x: -0.55, z: 0.06, r: 0.22, h: 0.38 },
    { x: 0.0, z: -0.04, r: 0.24, h: 0.42 },
    { x: 0.55, z: 0.05, r: 0.21, h: 0.36 },
  ];

  for (let bIdx = 0; bIdx < boxwoodCenters.length; bIdx++) {
    const bc = boxwoodCenters[bIdx];
    const shrubGroup = new T.Group();
    shrubGroup.position.set(bc.x, soilY + 0.02, bc.z);

    // Main mound geometry
    const coreGeo = new T.SphereGeometry(bc.r, 14, 10);
    coreGeo.scale(1.1, 0.95, 0.9);
    const coreMesh = new T.Mesh(coreGeo, boxwoodLeafMat);
    coreMesh.position.set(0, bc.h * 0.45, 0);
    coreMesh.castShadow = true;
    shrubGroup.add(coreMesh);

    // Textured leaf cluster bumps
    const numBumps = 8;
    const bumpGeo = new T.SphereGeometry(bc.r * 0.4, 8, 6);
    for (let i = 0; i < numBumps; i++) {
      const angle = (i / numBumps) * Math.PI * 2 + rand() * 0.5;
      const elev = 0.2 + rand() * 0.6;
      const dist = bc.r * 0.75;
      const bump = new T.Mesh(bumpGeo, i % 2 === 0 ? boxwoodLeafLightMat : boxwoodLeafMat);
      bump.position.set(
        Math.cos(angle) * dist,
        bc.h * 0.45 + Math.sin(elev * Math.PI) * bc.r * 0.4,
        Math.sin(angle) * dist * 0.8
      );
      bump.scale.set(0.8 + rand() * 0.4, 0.8 + rand() * 0.4, 0.8 + rand() * 0.4);
      bump.castShadow = true;
      shrubGroup.add(bump);
    }

    foliageGroup.add(shrubGroup);
    swayingElements.push({ obj: shrubGroup, baseRotZ: 0, amp: 0.015, freq: 1.2, phase: bIdx * 1.5 });
  }

  // B. Ornamental Pampas Grasses (Tall back accent with feathery cream plumes, height reaching ~1.30m total)
  // Planter total height top target ~1.30m -> height above soil ~ 0.90m
  const pampasClusters = [
    { x: -0.65, z: -0.10, count: 12, height: 0.85 },
    { x: -0.20, z: -0.12, count: 14, height: 0.90 },
    { x: 0.25, z: -0.11, count: 15, height: 0.88 },
    { x: 0.65, z: -0.08, count: 11, height: 0.82 },
  ];

  const bladeGeo = new T.ConeGeometry(0.015, 0.65, 5);
  const plumeGeo = new T.ConeGeometry(0.045, 0.32, 7);

  pampasClusters.forEach((cl, cIdx) => {
    const pGroup = new T.Group();
    pGroup.position.set(cl.x, soilY + 0.02, cl.z);

    // Arching green grass blades around base
    for (let i = 0; i < cl.count; i++) {
      const angle = (i / cl.count) * Math.PI * 2 + rand() * 0.4;
      const tilt = 0.2 + rand() * 0.35;
      const blade = new T.Mesh(bladeGeo, pampasStemMat);
      blade.position.set(0, 0.3, 0);
      blade.scale.set(0.8 + rand() * 0.4, 0.8 + rand() * 0.4, 0.8 + rand() * 0.4);
      
      const bladePivot = new T.Group();
      bladePivot.rotation.y = angle;
      bladePivot.rotation.z = tilt;
      bladePivot.add(blade);
      blade.castShadow = true;
      pGroup.add(bladePivot);
    }

    // Tall central stalks with feathery pampas plumes
    const numPlumes = 3 + Math.floor(rand() * 2);
    for (let p = 0; p < numPlumes; p++) {
      const plumeStalk = new T.Group();
      const stalkH = cl.height * (0.85 + rand() * 0.15);
      
      // Stem
      const stemMesh = new T.Mesh(
        new T.CylinderGeometry(0.008, 0.012, stalkH * 0.7, 5),
        pampasStemMat
      );
      stemMesh.position.y = (stalkH * 0.7) / 2;
      stemMesh.castShadow = true;
      plumeStalk.add(stemMesh);

      // Fluffy plume top
      const plumeTop = new T.Mesh(plumeGeo, pampasPlumeMat);
      plumeTop.position.y = stalkH * 0.7 + 0.14;
      plumeTop.scale.set(1.0 + rand() * 0.3, 1.0 + rand() * 0.2, 1.0 + rand() * 0.3);
      plumeTop.castShadow = true;
      plumeStalk.add(plumeTop);

      // Upper soft plume puffs for natural tapered look
      const puff1 = new T.Mesh(new T.SphereGeometry(0.035, 6, 5), pampasPlumeMat);
      puff1.position.set(0, stalkH * 0.7 + 0.05, 0);
      puff1.scale.set(1.0, 1.6, 0.9);
      plumeStalk.add(puff1);

      plumeStalk.position.set((rand() - 0.5) * 0.1, 0, (rand() - 0.5) * 0.08);
      plumeStalk.rotation.x = (rand() - 0.5) * 0.15;
      plumeStalk.rotation.z = (rand() - 0.5) * 0.18;

      pGroup.add(plumeStalk);
      swayingElements.push({
        obj: plumeStalk,
        baseRotZ: plumeStalk.rotation.z,
        baseRotX: plumeStalk.rotation.x,
        amp: 0.04 + rand() * 0.03,
        freq: 1.5 + rand() * 0.5,
        phase: cIdx * 1.8 + p * 0.7,
      });
    }

    foliageGroup.add(pGroup);
  });

  // C. Flowering Lavender (Clusters in front & between shrubs)
  const lavenderSpots = [
    { x: -0.75, z: 0.10, count: 6 },
    { x: -0.32, z: 0.12, count: 8 },
    { x: -0.15, z: 0.08, count: 5 },
    { x: 0.28, z: 0.12, count: 8 },
    { x: 0.42, z: 0.09, count: 6 },
    { x: 0.72, z: 0.10, count: 7 },
  ];

  const lavStemGeo = new T.CylinderGeometry(0.005, 0.007, 0.28, 5);
  const lavSpikeGeo = new T.ConeGeometry(0.022, 0.12, 6);
  const lavBaseFoliageGeo = new T.SphereGeometry(0.09, 8, 6);

  lavenderSpots.forEach((spot, sIdx) => {
    const lavGroup = new T.Group();
    lavGroup.position.set(spot.x, soilY + 0.02, spot.z);

    // Foliage clump at base
    const baseFol = new T.Mesh(lavBaseFoliageGeo, lavenderFoliageMat);
    baseFol.scale.set(1.2, 0.7, 1.0);
    baseFol.position.y = 0.05;
    baseFol.castShadow = true;
    lavGroup.add(baseFol);

    // Stems & purple flower spikes
    for (let k = 0; k < spot.count; k++) {
      const flowerStem = new T.Group();
      const sH = 0.26 + rand() * 0.08;
      
      const stem = new T.Mesh(lavStemGeo, lavenderFoliageMat);
      stem.position.y = sH / 2;
      flowerStem.add(stem);

      const spike = new T.Mesh(lavSpikeGeo, lavenderFlowerMat);
      spike.position.y = sH + 0.05;
      spike.castShadow = true;
      flowerStem.add(spike);

      const angle = (k / spot.count) * Math.PI * 2 + rand() * 0.3;
      const spread = 0.12 + rand() * 0.15;
      flowerStem.position.set(Math.cos(angle) * 0.04, 0, Math.sin(angle) * 0.04);
      flowerStem.rotation.x = Math.sin(angle) * spread;
      flowerStem.rotation.z = Math.cos(angle) * spread;

      lavGroup.add(flowerStem);
      swayingElements.push({
        obj: flowerStem,
        baseRotZ: flowerStem.rotation.z,
        baseRotX: flowerStem.rotation.x,
        amp: 0.035,
        freq: 2.0,
        phase: sIdx * 0.9 + k * 0.4,
      });
    }

    foliageGroup.add(lavGroup);
  });

  group.add(foliageGroup);

  // Set standard asset metadata
  group.userData.size = [1.80, 1.30, 0.55];

  // Animated sway tick function
  group.userData.tick = (t, dt) => {
    for (let i = 0; i < swayingElements.length; i++) {
      const el = swayingElements[i];
      const sway = Math.sin(t * el.freq + el.phase) * el.amp;
      const gust = Math.sin(t * 0.7 + el.phase * 0.5) * (el.amp * 0.5);
      if (el.baseRotZ !== undefined) {
        el.obj.rotation.z = el.baseRotZ + sway + gust;
      }
      if (el.baseRotX !== undefined) {
        el.obj.rotation.x = el.baseRotX + Math.cos(t * (el.freq * 0.8) + el.phase) * (el.amp * 0.4);
      }
    }
  };

  return group;
}
