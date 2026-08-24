// src/assets/city_tower.js — asset "CityTower": Stylized urban skyscraper tower with tiered architectural crown, reflective window mullions, and sunset-reflecting glass facades.
// Dimensions: 16.00 x 55.00 x 16.00 m (w x h x d), Y-up, centered on origin, base at y=0.
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildCityTower(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined) ? opts.seed : 42;
  const rand = mulberry32(seed + (opts.variant || 0) * 100);

  const group = new T.Group();
  group.name = 'CityTower';

  // Materials
  const glassMat = new T.MeshStandardMaterial({
    color: 0x22354a,
    roughness: 0.15,
    metalness: 0.85,
  });

  const warmGlassMat = new T.MeshStandardMaterial({
    color: 0xcc8245,
    emissive: 0x5a2d10,
    emissiveIntensity: 0.6,
    roughness: 0.25,
    metalness: 0.5,
  });

  const frameMat = new T.MeshStandardMaterial({
    color: 0x8a929e,
    roughness: 0.35,
    metalness: 0.7,
  });

  const darkMetalMat = new T.MeshStandardMaterial({
    color: 0x2b303a,
    roughness: 0.5,
    metalness: 0.8,
  });

  const baseStoneMat = new T.MeshStandardMaterial({
    color: 0x363c46,
    roughness: 0.85,
    metalness: 0.15,
  });

  const beaconMat = new T.MeshStandardMaterial({
    color: 0xff3b30,
    emissive: 0xff2200,
    emissiveIntensity: 2.0,
    roughness: 0.2,
  });

  // 1. Base / Podium (y: 0 to 10 m)
  // Plinth
  const plinthGeo = new T.BoxGeometry(16.0, 1.5, 16.0);
  const plinth = new T.Mesh(plinthGeo, baseStoneMat);
  plinth.position.y = 0.75;
  plinth.castShadow = plinth.receiveShadow = true;
  group.add(plinth);

  // Podium Core & Glass entrance
  const podiumCoreGeo = new T.BoxGeometry(15.4, 8.0, 15.4);
  const podiumCore = new T.Mesh(podiumCoreGeo, baseStoneMat);
  podiumCore.position.y = 5.5;
  podiumCore.castShadow = podiumCore.receiveShadow = true;
  group.add(podiumCore);

  // Podium Grand Cornice
  const corniceGeo = new T.BoxGeometry(16.0, 0.8, 16.0);
  const cornice = new T.Mesh(corniceGeo, frameMat);
  cornice.position.y = 9.9;
  cornice.castShadow = cornice.receiveShadow = true;
  group.add(cornice);

  // 2. Main Tower Shaft (y: 10.3 to 42.0 m)
  const shaftHeight = 31.7;
  const shaftCenterY = 10.3 + shaftHeight / 2; // 26.15

  // Glass Core
  const glassCoreGeo = new T.BoxGeometry(14.6, shaftHeight, 14.6);
  const glassCore = new T.Mesh(glassCoreGeo, glassMat);
  glassCore.position.y = shaftCenterY;
  glassCore.castShadow = glassCore.receiveShadow = true;
  group.add(glassCore);

  // 4 Corner Structural Columns (Width 16.0 m, Depth 16.0 m)
  const colW = 1.0;
  const colGeo = new T.BoxGeometry(colW, shaftHeight, colW);
  const colOffset = 8.0 - colW / 2; // 7.5

  const corners = [
    [-colOffset, colOffset],
    [colOffset, colOffset],
    [-colOffset, -colOffset],
    [colOffset, -colOffset],
  ];

  for (const [cx, cz] of corners) {
    const colMesh = new T.Mesh(colGeo, frameMat);
    colMesh.position.set(cx, shaftCenterY, cz);
    colMesh.castShadow = colMesh.receiveShadow = true;
    group.add(colMesh);
  }

  // Vertical Architectural Mullions on Facades
  const mullionThickness = 0.25;
  const mullionDepth = 0.4;
  const mullionGeoX = new T.BoxGeometry(mullionThickness, shaftHeight, mullionDepth);
  const mullionGeoZ = new T.BoxGeometry(mullionDepth, shaftHeight, mullionThickness);

  const mullionPositions = [-5.0, -2.5, 0, 2.5, 5.0];
  for (const offset of mullionPositions) {
    // North and South facades (Z = +/- 7.4)
    const mFront = new T.Mesh(mullionGeoX, frameMat);
    mFront.position.set(offset, shaftCenterY, 7.35);
    mFront.castShadow = true;
    group.add(mFront);

    const mBack = new T.Mesh(mullionGeoX, frameMat);
    mBack.position.set(offset, shaftCenterY, -7.35);
    mBack.castShadow = true;
    group.add(mBack);

    // East and West facades (X = +/- 7.4)
    const mRight = new T.Mesh(mullionGeoZ, frameMat);
    mRight.position.set(7.35, shaftCenterY, offset);
    mRight.castShadow = true;
    group.add(mRight);

    const mLeft = new T.Mesh(mullionGeoZ, frameMat);
    mLeft.position.set(-7.35, shaftCenterY, offset);
    mLeft.castShadow = true;
    group.add(mLeft);
  }

  // Horizontal Floor Spandrels & Warm Glowing Window Bands
  const numFloors = 8;
  const floorSpacing = shaftHeight / numFloors;
  const spandrelGeo = new T.BoxGeometry(15.0, 0.5, 15.0);
  const warmBandGeo = new T.BoxGeometry(14.7, 0.4, 14.7);

  for (let i = 0; i <= numFloors; i++) {
    const fy = 10.3 + i * floorSpacing;
    const spandrel = new T.Mesh(spandrelGeo, darkMetalMat);
    spandrel.position.y = fy;
    spandrel.castShadow = true;
    group.add(spandrel);

    if (i < numFloors && (i % 2 === 1 || rand() > 0.4)) {
      const warmBand = new T.Mesh(warmBandGeo, warmGlassMat);
      warmBand.position.y = fy + floorSpacing * 0.5;
      group.add(warmBand);
    }
  }

  // 3. Tier 1 Setback (y: 42.0 to 48.0 m)
  const tier1BaseY = 42.0;
  const tier1Height = 6.0;
  const tier1CenterY = tier1BaseY + tier1Height / 2; // 45.0

  // Tier 1 Balustrade / Transition Cornice
  const t1BalustradeGeo = new T.BoxGeometry(15.2, 0.6, 15.2);
  const t1Balustrade = new T.Mesh(t1BalustradeGeo, frameMat);
  t1Balustrade.position.y = tier1BaseY + 0.3;
  t1Balustrade.castShadow = true;
  group.add(t1Balustrade);

  // Tier 1 Shaft
  const t1ShaftGeo = new T.BoxGeometry(12.4, tier1Height, 12.4);
  const t1Shaft = new T.Mesh(t1ShaftGeo, glassMat);
  t1Shaft.position.y = tier1CenterY;
  t1Shaft.castShadow = t1Shaft.receiveShadow = true;
  group.add(t1Shaft);

  // Tier 1 Corner Pilasters
  const t1ColGeo = new T.BoxGeometry(0.8, tier1Height, 0.8);
  const t1ColOffset = 6.2 - 0.4; // 5.8
  for (const [cx, cz] of [[-t1ColOffset, t1ColOffset], [t1ColOffset, t1ColOffset], [-t1ColOffset, -t1ColOffset], [t1ColOffset, -t1ColOffset]]) {
    const t1Col = new T.Mesh(t1ColGeo, frameMat);
    t1Col.position.set(cx, tier1CenterY, cz);
    t1Col.castShadow = true;
    group.add(t1Col);
  }

  // Tier 1 Warm Glass Inset
  const t1WarmGeo = new T.BoxGeometry(12.5, 1.8, 12.5);
  const t1Warm = new T.Mesh(t1WarmGeo, warmGlassMat);
  t1Warm.position.y = tier1CenterY;
  group.add(t1Warm);

  // 4. Tier 2 Upper Crown & Louvers (y: 48.0 to 52.5 m)
  const tier2BaseY = 48.0;
  const tier2Height = 4.5;
  const tier2CenterY = tier2BaseY + tier2Height / 2; // 50.25

  const t2BalustradeGeo = new T.BoxGeometry(12.8, 0.5, 12.8);
  const t2Balustrade = new T.Mesh(t2BalustradeGeo, frameMat);
  t2Balustrade.position.y = tier2BaseY + 0.25;
  t2Balustrade.castShadow = true;
  group.add(t2Balustrade);

  const crownCoreGeo = new T.BoxGeometry(9.4, tier2Height, 9.4);
  const crownCore = new T.Mesh(crownCoreGeo, darkMetalMat);
  crownCore.position.y = tier2CenterY;
  crownCore.castShadow = true;
  group.add(crownCore);

  // Crown Louvers / Architectural Fins
  const louverGeo = new T.BoxGeometry(9.6, 0.25, 9.6);
  for (let li = 0; li < 4; li++) {
    const louver = new T.Mesh(louverGeo, frameMat);
    louver.position.y = tier2BaseY + 1.0 + li * 0.9;
    louver.castShadow = true;
    group.add(louver);
  }

  // 5. Crown Cap & Pinnacle Spire (y: 52.5 to 55.0 m)
  const capGeo = new T.CylinderGeometry(2.4, 4.2, 1.3, 8);
  const cap = new T.Mesh(capGeo, frameMat);
  cap.position.y = 52.5 + 0.65; // 53.15
  cap.castShadow = true;
  group.add(cap);

  // Antenna Needle / Spire: reaches y = 55.0 m
  const spireHeight = 55.0 - 53.8; // 1.2 m
  const spireGeo = new T.CylinderGeometry(0.08, 0.35, spireHeight, 8);
  const spire = new T.Mesh(spireGeo, frameMat);
  spire.position.y = 53.8 + spireHeight / 2; // 54.4
  spire.castShadow = true;
  group.add(spire);

  // Warning Beacon light at tip (y = 55.0)
  const beaconGeo = new T.SphereGeometry(0.18, 8, 8);
  const beacon = new T.Mesh(beaconGeo, beaconMat);
  beacon.position.y = 54.91;
  group.add(beacon);

  group.userData.size = [16.0, 55.0, 16.0];

  return group;
}

export function cityTowerParts(T = THREE) {
  // Provided for InstancedMesh compatibility
  const dummy = buildCityTower(T);
  const parts = [];
  dummy.traverse((child) => {
    if (child.isMesh) {
      parts.push({
        geometry: child.geometry.clone().applyMatrix4(child.matrix),
        material: child.material,
        castShadow: child.castShadow,
      });
    }
  });
  return parts;
}

export const city_towerParts = cityTowerParts;
