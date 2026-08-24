// src/assets/stone_lantern.js — asset "StoneLantern": Kasuga-style stone lantern (toro)
// with layered pedestal (kiso), central post (sao), middle platform (chudai),
// carved light box (hibukuro) holding a warm flickering light source,
// hexagonal umbrella roof (kasa) capped by a lotus finial (hoju).
// approx size 0.7 x 1.65 x 0.7 m (w x h x d); expected instances: ~3
// CONTRACT: export function buildStoneLantern(THREE, opts) → THREE.Group, origin at base (y=0), +Y up, +Z front, meters.

import * as THREE from 'three';

export function buildStoneLantern(T = THREE, opts = {}) {
  const g = new T.Group();
  g.name = 'StoneLantern';

  // Weathered granite material
  const stoneMat = new T.MeshStandardMaterial({
    color: 0x82807a,
    roughness: 0.88,
    metalness: 0.05
  });

  const darkStoneMat = new T.MeshStandardMaterial({
    color: 0x605e59,
    roughness: 0.92,
    metalness: 0.05
  });

  // Glowing warm paper / flame material inside hibukuro
  const lanternGlowMat = new T.MeshStandardMaterial({
    color: 0xffd285,
    emissive: 0xffa544,
    emissiveIntensity: 3.2,
    roughness: 0.35,
    metalness: 0.0
  });

  // 1. Base Pedestal (Kiso) - stepped hexagonal / faceted stones
  // Bottom stepped platform: 0.68 x 0.12 x 0.68 m
  const base1 = new T.Mesh(new T.CylinderGeometry(0.34, 0.36, 0.12, 6), darkStoneMat);
  base1.position.y = 0.06;
  base1.castShadow = base1.receiveShadow = true;
  g.add(base1);

  // Upper base tier: 0.52 x 0.10 x 0.52 m
  const base2 = new T.Mesh(new T.CylinderGeometry(0.24, 0.28, 0.10, 6), stoneMat);
  base2.position.y = 0.17;
  base2.castShadow = base2.receiveShadow = true;
  g.add(base2);

  // 2. Central Post (Sao) - tapered hexagonal column with central ring bulge
  const saoLower = new T.Mesh(new T.CylinderGeometry(0.14, 0.16, 0.40, 6), stoneMat);
  saoLower.position.y = 0.42;
  saoLower.castShadow = saoLower.receiveShadow = true;
  g.add(saoLower);

  const saoRing = new T.Mesh(new T.CylinderGeometry(0.17, 0.17, 0.06, 6), darkStoneMat);
  saoRing.position.y = 0.62;
  saoRing.castShadow = saoRing.receiveShadow = true;
  g.add(saoRing);

  const saoUpper = new T.Mesh(new T.CylinderGeometry(0.15, 0.14, 0.26, 6), stoneMat);
  saoUpper.position.y = 0.78;
  saoUpper.castShadow = saoUpper.receiveShadow = true;
  g.add(saoUpper);

  // 3. Middle Platform (Chudai) - flared hexagonal shelf supporting the light box
  const chudai = new T.Mesh(new T.CylinderGeometry(0.30, 0.18, 0.12, 6), darkStoneMat);
  chudai.position.y = 0.97;
  chudai.castShadow = chudai.receiveShadow = true;
  g.add(chudai);

  // 4. Light Box (Hibukuro) - hollow carved chamber with 6 corner pillars and glowing core
  const hibukuroBaseY = 1.03;
  const hibukuroHeight = 0.28;
  const pillarRadius = 0.22;
  const numPillars = 6;
  const pillarGeo = new T.BoxGeometry(0.045, hibukuroHeight, 0.045);

  for (let i = 0; i < numPillars; i++) {
    const angle = (i * Math.PI * 2) / numPillars;
    const px = Math.cos(angle) * pillarRadius;
    const pz = Math.sin(angle) * pillarRadius;
    const pillar = new T.Mesh(pillarGeo, stoneMat);
    pillar.position.set(px, hibukuroBaseY + hibukuroHeight / 2, pz);
    pillar.rotation.y = -angle;
    pillar.castShadow = pillar.receiveShadow = true;
    g.add(pillar);
  }

  // Glowing inner lantern core (fire/washi paper cylinder)
  const coreGeo = new T.CylinderGeometry(0.13, 0.13, hibukuroHeight - 0.02, 12);
  const coreMesh = new T.Mesh(coreGeo, lanternGlowMat);
  coreMesh.name = 'LanternCore';
  coreMesh.position.y = hibukuroBaseY + hibukuroHeight / 2;
  g.add(coreMesh);

  // Inner flame bead
  const flameGeo = new T.SphereGeometry(0.05, 8, 8);
  const flameMesh = new T.Mesh(flameGeo, lanternGlowMat);
  flameMesh.position.y = hibukuroBaseY + hibukuroHeight / 2;
  g.add(flameMesh);

  // 5. Umbrella Roof (Kasa) - sweeping hexagonal flared canopy (width approx 0.68 m)
  const kasaBottomY = hibukuroBaseY + hibukuroHeight;
  const kasaGeo = new T.ConeGeometry(0.35, 0.18, 6);
  const kasa = new T.Mesh(kasaGeo, stoneMat);
  kasa.position.y = kasaBottomY + 0.09;
  kasa.castShadow = kasa.receiveShadow = true;
  g.add(kasa);

  // Roof rim trim for authentic pagoda flare
  const rimGeo = new T.CylinderGeometry(0.35, 0.33, 0.04, 6);
  const rim = new T.Mesh(rimGeo, darkStoneMat);
  rim.position.y = kasaBottomY + 0.02;
  rim.castShadow = rim.receiveShadow = true;
  g.add(rim);

  // 6. Finial (Hoju / Kurin) - lotus bud top ornament
  const hojuBase = new T.Mesh(new T.CylinderGeometry(0.08, 0.10, 0.04, 6), darkStoneMat);
  hojuBase.position.y = kasaBottomY + 0.18 + 0.02;
  hojuBase.castShadow = hojuBase.receiveShadow = true;
  g.add(hojuBase);

  const hojuBall = new T.Mesh(new T.SphereGeometry(0.065, 10, 8), stoneMat);
  hojuBall.position.y = kasaBottomY + 0.24;
  hojuBall.scale.set(1.0, 1.25, 1.0);
  hojuBall.castShadow = hojuBall.receiveShadow = true;
  g.add(hojuBall);

  const hojuTip = new T.Mesh(new T.ConeGeometry(0.03, 0.07, 6), stoneMat);
  hojuTip.position.y = kasaBottomY + 0.32;
  hojuTip.castShadow = hojuTip.receiveShadow = true;
  g.add(hojuTip);

  // userData tick hook for material emissive flutter
  g.userData.materials = { glow: lanternGlowMat };
  g.userData.tick = (t) => {
    const flutter = 1.0 + 0.15 * Math.sin(t * 13.82) + 0.05 * Math.sin(t * 31.4);
    lanternGlowMat.emissiveIntensity = 3.2 * flutter;
  };

  return g;
}
