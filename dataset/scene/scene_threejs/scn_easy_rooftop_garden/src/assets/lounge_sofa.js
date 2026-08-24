// src/assets/lounge_sofa.js — asset "LoungeSofa": Low-profile sectional outdoor lounge sofa with charcoal teak frame, off-white weather-resistant cushions, and decorative mustard accent pillows.
// approx size 2.60 x 0.75 x 2.20 m (w x h x d)
// CONTRACT: export function buildLoungeSofa(THREE, opts = {}) → THREE.Group, origin at the base (y = 0), +Y up, +Z front, meters.

import * as THREE from 'three';

// Tiny deterministic PRNG (mulberry32)
function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildLoungeSofa(T = THREE, opts = {}) {
  const seed = opts.seed !== undefined ? opts.seed : 42;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'LoungeSofa';

  // Materials
  // Charcoal teak frame: dark warm grey / weathered charcoal wood
  const charcoalTeakMat = new T.MeshStandardMaterial({
    color: 0x2c2b2a,
    roughness: 0.85,
    metalness: 0.05
  });

  // Teak slat detail (slightly contrasting rich dark grain tone)
  const slatMat = new T.MeshStandardMaterial({
    color: 0x363432,
    roughness: 0.8,
    metalness: 0.05
  });

  // Off-white / cream weather-resistant outdoor fabric for seat and back cushions
  const cushionMat = new T.MeshStandardMaterial({
    color: 0xe8e4db,
    roughness: 0.9,
    metalness: 0.02
  });

  // Decorative mustard accent pillows
  const mustardPillowMat = new T.MeshStandardMaterial({
    color: 0xd49b28,
    roughness: 0.85,
    metalness: 0.02
  });

  // Secondary accent pillow (textured dark slate / charcoal throw pillow)
  const slatePillowMat = new T.MeshStandardMaterial({
    color: 0x4a5056,
    roughness: 0.9,
    metalness: 0.02
  });

  // Dimensions & Layout:
  // Overall bounds target: W=2.60m, H=0.75m, D=2.20m
  // Centred on origin (x ∈ [-1.30, 1.30], z ∈ [-1.10, 1.10], y ∈ [0, 0.75])
  //
  // L-shaped sectional configuration:
  // Main back section along the rear (z ~ -1.10 to -0.30, extending across x = -1.30 to +1.30)
  // Left chaise / return section along x = -1.30 to -0.50, extending forward towards z = +1.10.
  // Plus side integrated side-table / teak slatted ledge on the right side (x = +0.70 to +1.30).

  const baseThickness = 0.08;
  const legHeight = 0.12;
  const seatBaseY = legHeight; // 0.12
  const seatCushionHeight = 0.16;
  const backRestHeight = 0.44;
  const backRestThickness = 0.10;

  // Helper to create box meshes with shadows
  const createBox = (w, h, d, mat, px, py, pz, rx = 0, ry = 0, rz = 0) => {
    const mesh = new T.Mesh(new T.BoxGeometry(w, h, d), mat);
    mesh.position.set(px, py, pz);
    if (rx || ry || rz) mesh.rotation.set(rx, ry, rz);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  };

  // 1. Legs / Feet (Low-profile modern block legs)
  const legW = 0.08, legD = 0.08;
  const legPositions = [
    [-1.24, legHeight / 2, -1.04],
    [ 1.24, legHeight / 2, -1.04],
    [-1.24, legHeight / 2,  1.04],
    [-0.52, legHeight / 2,  1.04],
    [ 1.24, legHeight / 2, -0.32],
    [-0.52, legHeight / 2, -0.32],
    [ 0.36, legHeight / 2, -1.04],
    [ 0.36, legHeight / 2, -0.32],
  ];

  for (const [lx, ly, lz] of legPositions) {
    group.add(createBox(legW, legHeight, legD, charcoalTeakMat, lx, ly, lz));
  }

  // 2. Base Platforms (Charcoal teak plinth frames)
  // Main back section base plinth (spanning x: -1.30 to 1.30, z: -1.10 to -0.30) -> width 2.60, depth 0.80
  const mainBase = createBox(2.60, baseThickness, 0.80, charcoalTeakMat, 0.0, seatBaseY + baseThickness / 2, -0.70);
  group.add(mainBase);

  // Return chaise base plinth (spanning x: -1.30 to -0.50, z: -0.30 to 1.10) -> width 0.80, depth 1.40
  const chaiseBase = createBox(0.80, baseThickness, 1.40, charcoalTeakMat, -0.90, seatBaseY + baseThickness / 2, 0.40);
  group.add(chaiseBase);

  // Slatted teak detail on the extended right side platform (built-in side tray / table x: 0.70 to 1.30, z: -1.10 to -0.30)
  const slatW = 0.08, slatGap = 0.015, numSlats = 6;
  for (let i = 0; i < numSlats; i++) {
    const sx = 0.75 + i * (slatW + slatGap);
    if (sx < 1.28) {
      const slat = createBox(slatW, 0.015, 0.76, slatMat, sx, seatBaseY + baseThickness + 0.008, -0.70);
      group.add(slat);
    }
  }

  // 3. Backrests and Side Armrests (Charcoal teak low back frame)
  // Backrest along rear edge (z = -1.05, spanning x: -1.30 to 0.70)
  const rearBackFrame = createBox(2.00, backRestHeight, backRestThickness, charcoalTeakMat, -0.30, seatBaseY + baseThickness + backRestHeight / 2 - 0.01, -1.05);
  group.add(rearBackFrame);

  // Side backrest / arm along left edge (x = -1.25, spanning z: -1.10 to 1.10)
  const leftArmFrame = createBox(backRestThickness, backRestHeight, 2.20, charcoalTeakMat, -1.25, seatBaseY + baseThickness + backRestHeight / 2 - 0.01, 0.0);
  group.add(leftArmFrame);

  // Low end-cap armrest on the right side of seating area (x = 0.65, z: -1.10 to -0.30)
  const rightDivider = createBox(backRestThickness, backRestHeight * 0.75, 0.76, charcoalTeakMat, 0.65, seatBaseY + baseThickness + (backRestHeight * 0.75) / 2 - 0.01, -0.70);
  group.add(rightDivider);

  // 4. Seat Cushions (Thick, comfortable off-white cushions with beveled/rounded appearance)
  const cushionBaseY = seatBaseY + baseThickness + seatCushionHeight / 2;

  // Main middle seat cushions (2 modular seats along the back)
  const cWidth = 0.88;
  const cDepth = 0.72;
  const seatCushion1 = createBox(cWidth, seatCushionHeight, cDepth, cushionMat, -0.38, cushionBaseY, -0.66);
  const seatCushion2 = createBox(cWidth, seatCushionHeight, cDepth, cushionMat, 0.20, cushionBaseY, -0.66);
  group.add(seatCushion1, seatCushion2);

  // Corner / Chaise seat cushions
  const chaiseCushion1 = createBox(0.72, seatCushionHeight, 0.68, cushionMat, -0.84, cushionBaseY, 0.04);
  const chaiseCushion2 = createBox(0.72, seatCushionHeight, 0.68, cushionMat, -0.84, cushionBaseY, 0.72);
  group.add(chaiseCushion1, chaiseCushion2);

  // 5. Backrest Cushions (Angled/plump off-white cushions)
  const backCushionH = 0.38, backCushionT = 0.16;
  const backCushionY = seatBaseY + baseThickness + seatCushionHeight + backCushionH / 2 - 0.03;

  // Rear back cushions
  const backCush1 = createBox(0.68, backCushionH, backCushionT, cushionMat, -0.84, backCushionY, -0.92, -0.06, 0, 0);
  const backCush2 = createBox(0.68, backCushionH, backCushionT, cushionMat, -0.16, backCushionY, -0.92, -0.06, 0, 0);
  const backCush3 = createBox(0.56, backCushionH, backCushionT, cushionMat, 0.38, backCushionY, -0.92, -0.06, 0, 0);
  group.add(backCush1, backCush2, backCush3);

  // Left return back cushions
  const leftCush1 = createBox(backCushionT, backCushionH, 0.68, cushionMat, -1.12, backCushionY, -0.28, 0, 0, 0.06);
  const leftCush2 = createBox(backCushionT, backCushionH, 0.68, cushionMat, -1.12, backCushionY, 0.40, 0, 0, 0.06);
  group.add(leftCush1, leftCush2);

  // 6. Accent Pillows (Mustard yellow and slate accent throw pillows)
  const pillowSize = 0.34, pillowThick = 0.12;

  // Mustard pillow 1 (in corner junction)
  const pillow1 = createBox(pillowSize, pillowSize, pillowThick, mustardPillowMat, -0.80, backCushionY - 0.05, -0.78, 0.08, 0.45, -0.08);
  // Mustard pillow 2 (on chaise lounge section)
  const pillow2 = createBox(pillowSize, pillowSize, pillowThick, mustardPillowMat, -0.96, backCushionY - 0.08, 0.58, 0.15, -0.3, 0.1);
  // Slate pillow (on right seat)
  const pillow3 = createBox(pillowSize, pillowSize, pillowThick, slatePillowMat, 0.42, backCushionY - 0.06, -0.78, 0.05, -0.22, 0.05);
  // Additional small lumbar mustard pillow
  const pillow4 = createBox(0.36, 0.22, 0.10, mustardPillowMat, -0.15, backCushionY - 0.10, -0.80, -0.05, 0.1, 0);

  group.add(pillow1, pillow2, pillow3, pillow4);

  // Store measured bounds metadata
  group.userData.size = [2.60, 0.75, 2.20];

  return group;
}
