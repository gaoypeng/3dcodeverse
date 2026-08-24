// src/assets/pergola.js — asset "Pergola"
// Modern minimalist four-post timber pergola with slatted cedar roof beams and dark bronze metal joinery.
// Size: 4.60 x 3.00 x 4.60 m (w x h x d), centered on Y axis, standing on ground plane y = 0.
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildPergola(T = THREE, opts = {}) {
  const seed = typeof opts.seed === 'number' ? opts.seed : 101;
  const rand = mulberry32(seed);
  const variant = typeof opts.variant === 'number' ? opts.variant : 0;

  const group = new T.Group();
  group.name = 'Pergola';

  // --- Materials ---
  const postWoodMat = new T.MeshStandardMaterial({
    color: 0x8a4d29, // rich warm cedar post
    roughness: 0.68,
    metalness: 0.04,
  });

  const beamWoodMat = new T.MeshStandardMaterial({
    color: 0x96552e, // warm cedar main beams & rafters
    roughness: 0.65,
    metalness: 0.04,
  });

  const slatWoodMat = new T.MeshStandardMaterial({
    color: 0xa45f34, // sun-warmed cedar louver slats
    roughness: 0.6,
    metalness: 0.03,
  });

  const bronzeMetalMat = new T.MeshStandardMaterial({
    color: 0x2e2926, // dark bronze architectural joinery
    roughness: 0.38,
    metalness: 0.85,
  });

  const bronzeBoltMat = new T.MeshStandardMaterial({
    color: 0x1f1b18, // dark antique bronze hardware
    roughness: 0.32,
    metalness: 0.9,
  });

  // --- Dimensions ---
  const totalW = 4.60;
  const totalH = 3.00;
  const totalD = 4.60;

  // Post positions: 4 posts inset slightly from beam cantilever ends
  const postOffset = 1.95; // post centers at ±1.95m in X and Z (span 3.90m between centers)
  const postW = 0.18;
  const postH = 2.62; // from y = 0.02 (base plate) to y = 2.64
  const postY = postH / 2 + 0.02; // center y = 1.33

  // 1. Four Timber Posts + Bronze Base Shoes & Bolt Anchors
  const postsGroup = new T.Group();
  postsGroup.name = 'Posts';

  const postGeo = new T.BoxGeometry(postW, postH, postW);
  const basePlateGeo = new T.BoxGeometry(0.26, 0.02, 0.26);
  const baseCollarGeo = new T.BoxGeometry(0.20, 0.12, 0.20);
  const boltGeo = new T.CylinderGeometry(0.012, 0.012, 0.025, 8);

  const postCoords = [
    [-postOffset, -postOffset],
    [postOffset, -postOffset],
    [-postOffset, postOffset],
    [postOffset, postOffset],
  ];

  for (let i = 0; i < postCoords.length; i++) {
    const [px, pz] = postCoords[i];

    // Main vertical timber post
    const postMesh = new T.Mesh(postGeo, postWoodMat);
    postMesh.position.set(px, postY, pz);
    postMesh.castShadow = true;
    postMesh.receiveShadow = true;
    postsGroup.add(postMesh);

    // Bronze base plate
    const basePlate = new T.Mesh(basePlateGeo, bronzeMetalMat);
    basePlate.position.set(px, 0.01, pz);
    basePlate.castShadow = true;
    basePlate.receiveShadow = true;
    postsGroup.add(basePlate);

    // Bronze base collar wrap
    const baseCollar = new T.Mesh(baseCollarGeo, bronzeMetalMat);
    baseCollar.position.set(px, 0.07, pz);
    baseCollar.castShadow = true;
    postsGroup.add(baseCollar);

    // 4 Corner anchor bolts on base plate
    const boltOffset = 0.10;
    const boltLocalCoords = [
      [-boltOffset, -boltOffset],
      [boltOffset, -boltOffset],
      [-boltOffset, boltOffset],
      [boltOffset, boltOffset],
    ];
    for (const [bx, bz] of boltLocalCoords) {
      const bolt = new T.Mesh(boltGeo, bronzeBoltMat);
      bolt.position.set(px + bx, 0.025, pz + bz);
      postsGroup.add(bolt);
    }
  }
  group.add(postsGroup);

  // 2. Main Longitudinal Header Beams (Outer Perimeter Beams)
  // Two dual header beams running along Z (depth 4.60m) sandwiching the posts,
  // or primary beams along X and Z forming the structural box.
  const beamsGroup = new T.Group();
  beamsGroup.name = 'HeaderBeams';

  const beamH = 0.20;
  const beamW = 0.12;
  const beamY = 2.68; // center of main perimeter beams (y = 2.58 to 2.78)

  // Primary X-running lintel beams (span 4.60m along X at z = ±1.95m)
  const xBeamGeo = new T.BoxGeometry(totalW, beamH, beamW);
  const xBeamFront = new T.Mesh(xBeamGeo, beamWoodMat);
  xBeamFront.position.set(0, beamY, postOffset);
  xBeamFront.castShadow = true;
  xBeamFront.receiveShadow = true;
  beamsGroup.add(xBeamFront);

  const xBeamBack = new T.Mesh(xBeamGeo, beamWoodMat);
  xBeamBack.position.set(0, beamY, -postOffset);
  xBeamBack.castShadow = true;
  xBeamBack.receiveShadow = true;
  beamsGroup.add(xBeamBack);

  // Primary Z-running side fascia / rim beams (span 4.60m along Z at x = ±1.95m)
  const zBeamGeo = new T.BoxGeometry(beamW, beamH, totalD);
  const zBeamLeft = new T.Mesh(zBeamGeo, beamWoodMat);
  zBeamLeft.position.set(-postOffset, beamY, 0);
  zBeamLeft.castShadow = true;
  zBeamLeft.receiveShadow = true;
  beamsGroup.add(zBeamLeft);

  const zBeamRight = new T.Mesh(zBeamGeo, beamWoodMat);
  zBeamRight.position.set(postOffset, beamY, 0);
  zBeamRight.castShadow = true;
  zBeamRight.receiveShadow = true;
  beamsGroup.add(zBeamRight);

  // Outer border trim beams at exactly ±2.24m in X (for a solid frame feel)
  const outerBorderGeo = new T.BoxGeometry(0.08, beamH * 0.9, totalD);
  const outerBorderLeft = new T.Mesh(outerBorderGeo, beamWoodMat);
  outerBorderLeft.position.set(-2.26, beamY, 0);
  outerBorderLeft.castShadow = true;
  beamsGroup.add(outerBorderLeft);

  const outerBorderRight = new T.Mesh(outerBorderGeo, beamWoodMat);
  outerBorderRight.position.set(2.26, beamY, 0);
  outerBorderRight.castShadow = true;
  beamsGroup.add(outerBorderRight);

  group.add(beamsGroup);

  // 3. Dark Bronze Corner Joinery Brackets & Fastener Plates
  const joineryGroup = new T.Group();
  joineryGroup.name = 'MetalJoinery';

  const bracketCapGeo = new T.BoxGeometry(0.22, 0.24, 0.22);
  const gussetPlateGeo = new T.BoxGeometry(0.06, 0.16, 0.06);

  for (let i = 0; i < postCoords.length; i++) {
    const [px, pz] = postCoords[i];

    // Bronze connector sleeve wrapping the post-beam intersection
    const bracketCap = new T.Mesh(bracketCapGeo, bronzeMetalMat);
    bracketCap.position.set(px, beamY, pz);
    bracketCap.castShadow = true;
    joineryGroup.add(bracketCap);

    // Diagonal 45-deg minimalist knee brace gussets in dark bronze
    const sx = px > 0 ? -1 : 1;
    const sz = pz > 0 ? -1 : 1;

    const braceGeo = new T.BoxGeometry(0.05, 0.36, 0.05);
    const braceX = new T.Mesh(braceGeo, bronzeMetalMat);
    braceX.position.set(px + sx * 0.24, beamY - 0.22, pz);
    braceX.rotation.z = (sx * Math.PI) / 4;
    braceX.castShadow = true;
    joineryGroup.add(braceX);

    const braceZ = new T.Mesh(braceGeo, bronzeMetalMat);
    braceZ.position.set(px, beamY - 0.22, pz + sz * 0.24);
    braceZ.rotation.x = (-sz * Math.PI) / 4;
    braceZ.castShadow = true;
    joineryGroup.add(braceZ);

    // Decorative bolt hardware along the bracket face
    const plateBolt = new T.Mesh(boltGeo, bronzeBoltMat);
    plateBolt.rotation.z = Math.PI / 2;
    plateBolt.position.set(px + (px > 0 ? 0.115 : -0.115), beamY, pz);
    joineryGroup.add(plateBolt);
  }
  group.add(joineryGroup);

  // 4. Primary Transverse Rafters (Cross Joists)
  // 7 cross joists spanning along X (4.60m) resting across the frame
  const raftersGroup = new T.Group();
  raftersGroup.name = 'CrossRafters';

  const rafterCount = 7;
  const rafterW = totalW;
  const rafterH = 0.12;
  const rafterD = 0.08;
  const rafterY = 2.82; // center y = 2.82 (y = 2.76 to 2.88)
  const rafterGeo = new T.BoxGeometry(rafterW, rafterH, rafterD);

  const rafterZMin = -2.15;
  const rafterZMax = 2.15;
  const rafterStepZ = (rafterZMax - rafterZMin) / (rafterCount - 1);

  for (let r = 0; r < rafterCount; r++) {
    const rz = rafterZMin + r * rafterStepZ;
    const rafter = new T.Mesh(rafterGeo, beamWoodMat);
    rafter.position.set(0, rafterY, rz);
    rafter.castShadow = true;
    rafter.receiveShadow = true;
    raftersGroup.add(rafter);

    // Small metal clip at rafter/beam intersection
    for (const px of [-postOffset, postOffset]) {
      const clip = new T.Mesh(new T.BoxGeometry(0.14, 0.03, 0.09), bronzeMetalMat);
      clip.position.set(px, rafterY - 0.06, rz);
      raftersGroup.add(clip);
    }
  }
  group.add(raftersGroup);

  // 5. Overhead Cedar Sun-Shade Slats / Louvers (The Iconic Slatted Canopy)
  // 21 evenly spaced slats running along Z (4.60m length) across the top.
  // Top reaches exactly y = 3.00m! (y = 2.88 to 3.00, height = 0.12m, center y = 2.94m)
  const slatsGroup = new T.Group();
  slatsGroup.name = 'RoofSlats';

  const slatCount = 21;
  const slatW = 0.045;
  const slatH = 0.12;
  const slatD = totalD;
  const slatY = 2.94; // y from 2.88 to 3.00
  const slatGeo = new T.BoxGeometry(slatW, slatH, slatD);

  const slatXMin = -2.20;
  const slatXMax = 2.20;
  const slatStepX = (slatXMax - slatXMin) / (slatCount - 1);

  // InstancedMesh for the 21 roof slats for optimal draw call efficiency
  const slatInst = new T.InstancedMesh(slatGeo, slatWoodMat, slatCount);
  slatInst.name = 'SlatInstances';
  slatInst.castShadow = true;
  slatInst.receiveShadow = true;

  const dummy = new T.Object3D();
  for (let s = 0; s < slatCount; s++) {
    const sx = slatXMin + s * slatStepX;
    dummy.position.set(sx, slatY, 0);
    dummy.rotation.set(0, 0, 0);
    dummy.scale.set(1, 1, 1);
    dummy.updateMatrix();
    slatInst.setMatrixAt(s, dummy.matrix);
  }
  slatInst.instanceMatrix.needsUpdate = true;
  slatsGroup.add(slatInst);

  group.add(slatsGroup);

  // 6. Optional Side Trellis Privacy Slat Wall (Variant or Accent feature)
  // Modern vertical cedar slat accent feature between two back posts
  if (variant === 1 || variant === 0) {
    const accentWall = new T.Group();
    accentWall.name = 'AccentTrellis';

    const trellisSlatCount = 9;
    const trellisSlatGeo = new T.BoxGeometry(0.035, 1.80, 0.035);
    const trellisY = 1.30; // y = 0.40 to 2.20
    const trellisZ = -postOffset;
    const trellisXMin = -1.50;
    const trellisXMax = 1.50;
    const trellisStep = (trellisXMax - trellisXMin) / (trellisSlatCount - 1);

    const railGeo = new T.BoxGeometry(3.20, 0.04, 0.05);
    const topRail = new T.Mesh(railGeo, bronzeMetalMat);
    topRail.position.set(0, 2.22, trellisZ);
    topRail.castShadow = true;
    accentWall.add(topRail);

    const botRail = new T.Mesh(railGeo, bronzeMetalMat);
    botRail.position.set(0, 0.38, trellisZ);
    botRail.castShadow = true;
    accentWall.add(botRail);

    const trellisInst = new T.InstancedMesh(trellisSlatGeo, slatWoodMat, trellisSlatCount);
    trellisInst.castShadow = true;
    trellisInst.receiveShadow = true;

    for (let ts = 0; ts < trellisSlatCount; ts++) {
      const tx = trellisXMin + ts * trellisStep;
      dummy.position.set(tx, trellisY, trellisZ);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.set(1, 1, 1);
      dummy.updateMatrix();
      trellisInst.setMatrixAt(ts, dummy.matrix);
    }
    trellisInst.instanceMatrix.needsUpdate = true;
    accentWall.add(trellisInst);

    group.add(accentWall);
  }

  // Set userData size for caller inspection
  const bbox = new T.Box3().setFromObject(group);
  const size = new T.Vector3();
  bbox.getSize(size);
  group.userData.size = [size.x, size.y, size.z];

  // Optional subtle motion hook
  group.userData.tick = (t, dt) => {};
  group.userData.update = group.userData.tick;

  return group;
}
