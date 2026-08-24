// src/assets/arched_bridge.js — asset "ArchedBridge"
// Traditional Japanese arched wooden bridge (Taiko-bashi) with weathered cedar planking,
// curved dark wood side stringers, vertical balusters, low railings, and four corner posts with giboshi finials.
// Size: 1.40 x 1.35 x 4.80 m (w x h x d)
// Coordinate frame: Y is UP, +Z is front, +X is right. Ground contact at y = 0.
import * as THREE from 'three';

function createRNG(seed = 42) {
  let s = (seed >>> 0) || 42;
  return function () {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildArchedBridge(T = THREE, opts = {}) {
  const seed = opts.seed ?? 42;
  const rand = createRNG(seed);

  const group = new T.Group();
  group.name = 'ArchedBridge';

  // Materials palette
  const darkWoodMat = new T.MeshStandardMaterial({
    color: 0x3a2518,
    roughness: 0.78,
    metalness: 0.08
  });

  const cedarMats = [
    new T.MeshStandardMaterial({ color: 0x7a604b, roughness: 0.85, metalness: 0.04 }),
    new T.MeshStandardMaterial({ color: 0x6f5542, roughness: 0.88, metalness: 0.04 }),
    new T.MeshStandardMaterial({ color: 0x846b54, roughness: 0.82, metalness: 0.04 })
  ];

  const ribWoodMat = new T.MeshStandardMaterial({
    color: 0x4f3624,
    roughness: 0.8,
    metalness: 0.06
  });

  const bronzeMat = new T.MeshStandardMaterial({
    color: 0x8a703d,
    roughness: 0.38,
    metalness: 0.82
  });

  const ironMat = new T.MeshStandardMaterial({
    color: 0x242426,
    roughness: 0.65,
    metalness: 0.75
  });

  const stoneMat = new T.MeshStandardMaterial({
    color: 0x58554e,
    roughness: 0.94,
    metalness: 0.05
  });

  // Arch geometry parameters
  // Deck spans z from -2.32 to +2.32 (with entrance sills reaching z = ±2.40)
  // Total span = 4.80 m. Half span L_half = 2.32
  const L_half = 2.32;
  const H_apex = 0.58; // Deck surface height at center (z = 0)
  const H_end = 0.08;  // Deck surface height at ends (z = ±L_half)

  function deckY(z) {
    const u = Math.min(1.0, Math.max(0.0, Math.abs(z) / L_half));
    return H_end + (H_apex - H_end) * (1.0 - u * u);
  }

  function deckTangentAngle(z) {
    // dy/dz = -2 * (H_apex - H_end) * z / (L_half^2)
    const slope = -2.0 * (H_apex - H_end) * z / (L_half * L_half);
    return Math.atan(slope);
  }

  // 1. Foundation stone footings at the 4 corners & 2 center support piers
  const footingGeo = new T.BoxGeometry(0.24, 0.16, 0.30);
  const cornerPositions = [
    [-0.58, 0.08, -2.25],
    [0.58, 0.08, -2.25],
    [-0.58, 0.08, 2.25],
    [0.58, 0.08, 2.25]
  ];
  for (const [fx, fy, fz] of cornerPositions) {
    const footing = new T.Mesh(footingGeo, stoneMat);
    footing.position.set(fx, fy, fz);
    footing.castShadow = true;
    footing.receiveShadow = true;
    group.add(footing);
  }

  // Entrance stone / timber approach ramps at z = ±2.36 (reaching down to y = 0)
  const rampGeo = new T.BoxGeometry(1.24, 0.08, 0.18);
  for (const s of [-1, 1]) {
    const ramp = new T.Mesh(rampGeo, darkWoodMat);
    ramp.position.set(0, 0.04, s * 2.31);
    ramp.rotation.x = s * 0.25;
    ramp.castShadow = true;
    ramp.receiveShadow = true;
    group.add(ramp);
  }

  // 2. Main Curved Stringers (Side Girders) at x = ±0.57
  const stringerX = 0.57;
  const numStringerSegments = 28;
  const stringerWidth = 0.08;
  const stringerHeight = 0.15;
  const stringerBoxGeo = new T.BoxGeometry(stringerWidth, stringerHeight, (2 * L_half) / numStringerSegments + 0.03);

  for (let i = 0; i < numStringerSegments; i++) {
    const z1 = -L_half + (i / numStringerSegments) * (2 * L_half);
    const z2 = -L_half + ((i + 1) / numStringerSegments) * (2 * L_half);
    const zMid = (z1 + z2) * 0.5;
    const yMid = deckY(zMid) - stringerHeight * 0.5 - 0.02;
    const rotX = deckTangentAngle(zMid);

    for (const side of [-1, 1]) {
      const seg = new T.Mesh(stringerBoxGeo, darkWoodMat);
      seg.position.set(side * stringerX, yMid, zMid);
      seg.rotation.x = rotX;
      seg.castShadow = true;
      seg.receiveShadow = true;
      group.add(seg);
    }
  }

  // Transverse tie-beams (cross girders) underneath the stringers
  const tieBeamGeo = new T.BoxGeometry(1.36, 0.09, 0.09);
  const tieBeamEndCapGeo = new T.BoxGeometry(0.04, 0.11, 0.11);
  const tieZs = [-2.0, -1.33, -0.67, 0.0, 0.67, 1.33, 2.0];
  for (const tz of tieZs) {
    const ty = deckY(tz) - stringerHeight - 0.04;
    const tie = new T.Mesh(tieBeamGeo, darkWoodMat);
    tie.position.set(0, ty, tz);
    tie.castShadow = true;
    tie.receiveShadow = true;
    group.add(tie);

    for (const side of [-1, 1]) {
      const cap = new T.Mesh(tieBeamEndCapGeo, ironMat);
      cap.position.set(side * 0.68, ty, tz);
      cap.castShadow = true;
      group.add(cap);
    }
  }

  // 3. Deck Planking (Hashi-ita)
  const numPlanks = 40;
  const plankWidth = 1.14; // across X
  const plankThick = 0.038;
  const plankStepZ = (2 * L_half) / numPlanks;
  const plankDepth = plankStepZ * 0.88;
  const plankGeo = new T.BoxGeometry(plankWidth, plankThick, plankDepth);

  const battenGeo = new T.BoxGeometry(plankWidth * 0.96, 0.022, 0.035);

  for (let i = 0; i < numPlanks; i++) {
    const z = -L_half + (i + 0.5) * plankStepZ;
    const y = deckY(z) - plankThick * 0.5;
    const rotX = deckTangentAngle(z);

    const mat = cedarMats[Math.floor(rand() * cedarMats.length)];
    const plank = new T.Mesh(plankGeo, mat);
    plank.position.set(0, y, z);
    plank.rotation.x = rotX;
    plank.castShadow = true;
    plank.receiveShadow = true;
    group.add(plank);

    // Anti-slip batten / step cleat every 3rd plank
    if (i % 3 === 1) {
      const batten = new T.Mesh(battenGeo, ribWoodMat);
      batten.position.set(0, y + plankThick * 0.5 + 0.011, z);
      batten.rotation.x = rotX;
      batten.castShadow = true;
      batten.receiveShadow = true;
      group.add(batten);
    }
  }

  // Deck curb edges (narrow dark border timber along left and right deck edges)
  const curbGeo = new T.BoxGeometry(0.045, 0.05, (2 * L_half) / numStringerSegments + 0.02);
  for (let i = 0; i < numStringerSegments; i++) {
    const zMid = -L_half + (i + 0.5) * ((2 * L_half) / numStringerSegments);
    const yMid = deckY(zMid) + 0.02;
    const rotX = deckTangentAngle(zMid);

    for (const side of [-1, 1]) {
      const curb = new T.Mesh(curbGeo, darkWoodMat);
      curb.position.set(side * 0.56, yMid, zMid);
      curb.rotation.x = rotX;
      curb.castShadow = true;
      curb.receiveShadow = true;
      group.add(curb);
    }
  }

  // 4. Corner Posts (Oyabashira) & Intermediate Posts
  const postSize = 0.09;
  const railX = 0.63; // X distance for railings and posts (total bridge width ~ 1.40 m)

  // Function to create an authentic Giboshi finial
  function createGiboshi(scale = 1.0) {
    const gibGroup = new T.Group();

    // Base collar ring
    const collar = new T.Mesh(new T.CylinderGeometry(0.055 * scale, 0.06 * scale, 0.03 * scale, 12), ironMat);
    collar.position.y = 0.015 * scale;
    collar.castShadow = true;
    gibGroup.add(collar);

    // Neck
    const neck = new T.Mesh(new T.CylinderGeometry(0.04 * scale, 0.05 * scale, 0.025 * scale, 12), bronzeMat);
    neck.position.y = 0.04 * scale;
    neck.castShadow = true;
    gibGroup.add(neck);

    // Bulbous body (lotus bud)
    const bulb = new T.Mesh(new T.SphereGeometry(0.06 * scale, 12, 10), bronzeMat);
    bulb.position.y = 0.09 * scale;
    bulb.scale.set(1.0, 1.25, 1.0);
    bulb.castShadow = true;
    gibGroup.add(bulb);

    // Pointed top spike
    const tip = new T.Mesh(new T.ConeGeometry(0.03 * scale, 0.06 * scale, 12), bronzeMat);
    tip.position.y = 0.17 * scale;
    tip.castShadow = true;
    gibGroup.add(tip);

    return gibGroup;
  }

  // 4 Main Corner Posts
  const cornerZList = [-2.24, 2.24];
  for (const cz of cornerZList) {
    for (const side of [-1, 1]) {
      const cx = side * railX;
      const baseDeckY = deckY(cz);
      const postHeight = 0.80;
      const postGeo = new T.BoxGeometry(postSize, postHeight, postSize);
      const post = new T.Mesh(postGeo, darkWoodMat);
      post.position.set(cx, baseDeckY + postHeight * 0.5 - 0.06, cz);
      post.castShadow = true;
      post.receiveShadow = true;
      group.add(post);

      // Bronze collar bracket at post base
      const bCollar = new T.Mesh(new T.BoxGeometry(postSize + 0.02, 0.04, postSize + 0.02), ironMat);
      bCollar.position.set(cx, baseDeckY + 0.02, cz);
      bCollar.castShadow = true;
      group.add(bCollar);

      // Top Giboshi
      const gib = createGiboshi(0.9);
      gib.position.set(cx, baseDeckY + postHeight - 0.06, cz);
      group.add(gib);
    }
  }

  // Intermediate vertical posts (6 per side: at z = ±1.50, ±0.75, 0)
  const intermediateZs = [-1.50, -0.75, 0.0, 0.75, 1.50];
  for (const iz of intermediateZs) {
    for (const side of [-1, 1]) {
      const ix = side * railX;
      const dY = deckY(iz);
      const isApex = Math.abs(iz) < 0.01;
      const pHeight = isApex ? 0.74 : 0.70;
      const pGeo = new T.BoxGeometry(0.07, pHeight, 0.07);
      const iPost = new T.Mesh(pGeo, darkWoodMat);
      iPost.position.set(ix, dY + pHeight * 0.5 - 0.04, iz);
      iPost.castShadow = true;
      iPost.receiveShadow = true;
      group.add(iPost);

      // Apex posts get a refined bronze cap at peak y = 1.35
      if (isApex) {
        const gib = createGiboshi(0.7);
        gib.position.set(ix, dY + pHeight - 0.04, iz);
        group.add(gib);
      }
    }
  }

  // 5. Curved Handrails (Kasagi - top rail, and Hirageshi - lower rail)
  const numRailSegs = 28;
  const topRailGeo = new T.BoxGeometry(0.08, 0.06, (2 * L_half) / numRailSegs + 0.02);
  const midRailGeo = new T.BoxGeometry(0.05, 0.04, (2 * L_half) / numRailSegs + 0.02);
  const botRailGeo = new T.BoxGeometry(0.05, 0.04, (2 * L_half) / numRailSegs + 0.02);

  for (let i = 0; i < numRailSegs; i++) {
    const zMid = -L_half + (i + 0.5) * ((2 * L_half) / numRailSegs);
    const dY = deckY(zMid);
    const rotX = deckTangentAngle(zMid);

    const topY = dY + 0.66;
    const midY = dY + 0.40;
    const botY = dY + 0.16;

    for (const side of [-1, 1]) {
      const rx = side * railX;

      // Top rail
      const tRail = new T.Mesh(topRailGeo, darkWoodMat);
      tRail.position.set(rx, topY, zMid);
      tRail.rotation.x = rotX;
      tRail.castShadow = true;
      tRail.receiveShadow = true;
      group.add(tRail);

      // Mid rail
      const mRail = new T.Mesh(midRailGeo, ribWoodMat);
      mRail.position.set(rx, midY, zMid);
      mRail.rotation.x = rotX;
      mRail.castShadow = true;
      group.add(mRail);

      // Lower rail
      const bRail = new T.Mesh(botRailGeo, ribWoodMat);
      bRail.position.set(rx, botY, zMid);
      bRail.rotation.x = rotX;
      bRail.castShadow = true;
      group.add(bRail);
    }
  }

  // 6. Vertical Balusters (Koma-tsunagi) between lower rail and mid/top rail
  const numBalustersPerSide = 32;
  const balusterGeo = new T.CylinderGeometry(0.016, 0.016, 0.24, 8);
  const balStep = (2 * L_half - 0.2) / numBalustersPerSide;

  for (let i = 0; i < numBalustersPerSide; i++) {
    const bz = -L_half + 0.1 + (i + 0.5) * balStep;
    // Skip if too close to intermediate posts
    if (intermediateZs.some((iz) => Math.abs(bz - iz) < 0.08) || cornerZList.some((cz) => Math.abs(bz - cz) < 0.08)) {
      continue;
    }
    const dY = deckY(bz);
    const by = dY + 0.28;

    for (const side of [-1, 1]) {
      const bal = new T.Mesh(balusterGeo, darkWoodMat);
      bal.position.set(side * railX, by, bz);
      bal.castShadow = true;
      group.add(bal);
    }
  }

  // Size tag for contract
  group.userData.size = [1.40, 1.35, 4.80];

  return group;
}
