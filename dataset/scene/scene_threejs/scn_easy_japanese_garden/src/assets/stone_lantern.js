// src/assets/stone_lantern.js — Yukimi-doro (snow-viewing) style carved granite lantern
// approx size 0.75 x 1.40 x 0.75 m (w x h x d)
// CONTRACT: export function buildStoneLantern(THREE, opts = {}) → THREE.Group
// Origin at the base (y = 0), +Y up, +Z front, meters.

import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildStoneLantern(THREE_LIB = THREE, opts = {}) {
  const T = THREE_LIB;
  const seed = opts.seed !== undefined ? opts.seed : 42;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'StoneLantern';

  // Stone granite materials with subtle color variations based on seed
  const stoneHueShift = (rand() - 0.5) * 0.04;
  const stoneColor = new T.Color(0x8a8780).offsetHSL(0, 0, stoneHueShift);
  const stoneAccentColor = new T.Color(0x73706a).offsetHSL(0, 0, stoneHueShift);
  const mossTint = new T.Color(0x757b68).offsetHSL(0, 0, stoneHueShift);

  const stoneMat = new T.MeshStandardMaterial({
    color: stoneColor,
    roughness: 0.9,
    metalness: 0.05,
  });

  const stoneDarkMat = new T.MeshStandardMaterial({
    color: stoneAccentColor,
    roughness: 0.95,
    metalness: 0.04,
  });

  const mossyMat = new T.MeshStandardMaterial({
    color: mossTint,
    roughness: 0.92,
    metalness: 0.03,
  });

  // Glowing lantern interior material
  const glowMat = new T.MeshStandardMaterial({
    color: 0xffdd88,
    emissive: 0xff8c1a,
    emissiveIntensity: 2.2,
    roughness: 0.4,
  });

  const paperMat = new T.MeshStandardMaterial({
    color: 0xfffae0,
    emissive: 0xffa033,
    emissiveIntensity: 0.85,
    roughness: 0.7,
  });

  // Helper to add shadow flags
  const addMesh = (geo, mat, yPos = 0, rotY = 0) => {
    const mesh = new T.Mesh(geo, mat);
    mesh.position.y = yPos;
    mesh.rotation.y = rotY;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
    return mesh;
  };

  // --- 1. BASE / LEGS (y: 0.00 -> 0.28) ---
  // Stepped hexagonal ground platform / feet
  const basePlinth = new T.CylinderGeometry(0.24, 0.27, 0.06, 6);
  addMesh(basePlinth, stoneDarkMat, 0.03);

  // 6 Arched foot supports around base perimeter
  const footCount = 6;
  const footDist = 0.20;
  for (let i = 0; i < footCount; i++) {
    const angle = (i / footCount) * Math.PI * 2 + Math.PI / 6;
    const footGeo = new T.BoxGeometry(0.08, 0.12, 0.09);
    const foot = new T.Mesh(footGeo, stoneMat);
    foot.position.set(Math.cos(angle) * footDist, 0.06, Math.sin(angle) * footDist);
    foot.rotation.y = -angle;
    foot.castShadow = true;
    foot.receiveShadow = true;
    group.add(foot);
  }

  // Base pedestal body (hexagonal flared cylinder)
  const baseBody = new T.CylinderGeometry(0.18, 0.23, 0.18, 6);
  addMesh(baseBody, stoneMat, 0.16);

  // Base top rim
  const baseRim = new T.CylinderGeometry(0.20, 0.18, 0.04, 6);
  addMesh(baseRim, stoneDarkMat, 0.26);

  // --- 2. PEDESTAL / POST (Sao) (y: 0.26 -> 0.52) ---
  // Central rounded column with subtle fluting / rings
  const postCol = new T.CylinderGeometry(0.125, 0.14, 0.24, 16);
  addMesh(postCol, stoneMat, 0.38);

  const postRingMid = new T.CylinderGeometry(0.14, 0.14, 0.04, 16);
  addMesh(postRingMid, stoneDarkMat, 0.38);

  // --- 3. MIDDLE PLATFORM (Chudai) (y: 0.50 -> 0.63) ---
  // Lotus underside flare (Ukebana style bracket)
  const chudaiUnder = new T.CylinderGeometry(0.26, 0.13, 0.07, 6);
  addMesh(chudaiUnder, stoneMat, 0.53);

  // Main hexagonal shelf
  const chudaiShelf = new T.CylinderGeometry(0.26, 0.26, 0.05, 6);
  addMesh(chudaiShelf, stoneMat, 0.585);

  // Raised shelf lip
  const chudaiLip = new T.CylinderGeometry(0.27, 0.25, 0.03, 6);
  addMesh(chudaiLip, stoneDarkMat, 0.615);

  // --- 4. LIGHT CHAMBER (Hibukuro) (y: 0.62 -> 0.89) ---
  // Floor of chamber
  const chamberFloor = new T.CylinderGeometry(0.19, 0.20, 0.03, 6);
  addMesh(chamberFloor, stoneDarkMat, 0.635);

  // 6 Carved corner posts
  const pillarRadius = 0.175;
  const pillarGeo = new T.CylinderGeometry(0.018, 0.02, 0.24, 8);
  for (let i = 0; i < 6; i++) {
    const angle = (i / 6) * Math.PI * 2 + Math.PI / 6;
    const pillar = new T.Mesh(pillarGeo, stoneMat);
    pillar.position.set(Math.cos(angle) * pillarRadius, 0.75, Math.sin(angle) * pillarRadius);
    pillar.castShadow = true;
    pillar.receiveShadow = true;
    group.add(pillar);
  }

  // Translucent / shoji paper inner walls with warm glow
  const paperWalls = new T.CylinderGeometry(0.165, 0.165, 0.21, 6, 1, true);
  addMesh(paperWalls, paperMat, 0.755);

  // Glowing flame / lamp core in center
  const flameGeo = new T.CylinderGeometry(0.04, 0.06, 0.12, 12);
  const flameMesh = addMesh(flameGeo, glowMat, 0.75);

  // Warm point light radiating through windows
  const light = new T.PointLight(0xffaa44, 1.2, 3.5, 1.2);
  light.position.set(0, 0.76, 0);
  light.castShadow = false; // keep fast performance in multi-instance scenes
  group.add(light);

  // Chamber ceiling lintel
  const chamberCeil = new T.CylinderGeometry(0.22, 0.20, 0.04, 6);
  addMesh(chamberCeil, stoneDarkMat, 0.875);

  // --- 5. PAGODA ROOF (Kasa) (y: 0.88 -> 1.18, max width 0.75m) ---
  // Broad sweeping hexagonal pagoda roof (Yukimi style)
  // Low wide eaves: radius 0.375m (width 0.75m)
  const roofEaves = new T.CylinderGeometry(0.31, 0.375, 0.05, 6);
  addMesh(roofEaves, stoneMat, 0.91);

  // Mid slope
  const roofMid = new T.CylinderGeometry(0.21, 0.31, 0.11, 6);
  addMesh(roofMid, stoneMat, 0.985);

  // Top slope
  const roofTop = new T.CylinderGeometry(0.11, 0.21, 0.11, 6);
  addMesh(roofTop, mossyMat, 1.085);

  // 6 Decorative corner hip ridges
  const ridgeGeo = new T.CylinderGeometry(0.016, 0.02, 0.32, 6);
  for (let i = 0; i < 6; i++) {
    const angle = (i / 6) * Math.PI * 2 + Math.PI / 6;
    const ridge = new T.Mesh(ridgeGeo, stoneDarkMat);
    // Position halfway along slope, angled along hip rafter
    ridge.position.set(Math.cos(angle) * 0.23, 1.01, Math.sin(angle) * 0.23);
    ridge.rotation.y = -angle;
    ridge.rotation.z = 0.78; // incline along roof slope
    ridge.castShadow = true;
    group.add(ridge);
  }

  // --- 6. FINIAL (Hoju & Ukebana) (y: 1.17 -> 1.40) ---
  // Lotus flower collar / base (Ukebana)
  const finialBase = new T.CylinderGeometry(0.08, 0.11, 0.05, 8);
  addMesh(finialBase, stoneDarkMat, 1.18);

  const finialBead = new T.SphereGeometry(0.065, 12, 10);
  finialBead.scale(1, 0.7, 1);
  addMesh(finialBead, stoneMat, 1.225);

  // Sacred jewel (Hoju) onion/lotus-bud top finial tapering to point at 1.40m
  const hojuBody = new T.SphereGeometry(0.075, 12, 10);
  hojuBody.scale(1, 1.1, 1);
  addMesh(hojuBody, stoneMat, 1.28);

  const hojuSpire = new T.ConeGeometry(0.04, 0.12, 10);
  addMesh(hojuSpire, stoneMat, 1.34);

  // Measured size metadata
  group.userData.size = [0.75, 1.40, 0.75];

  // Subtle flame flicker animation
  group.userData.tick = (t) => {
    const flick = 1.0 + 0.12 * Math.sin(t * 7.5 + seed) + 0.08 * Math.sin(t * 13.7 + seed * 2);
    light.intensity = 1.2 * flick;
    glowMat.emissiveIntensity = 2.2 * flick;
    paperMat.emissiveIntensity = 0.85 * flick;
  };

  return group;
}
