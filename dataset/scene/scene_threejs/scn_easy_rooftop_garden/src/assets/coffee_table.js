// src/assets/coffee_table.js — asset "CoffeeTable"
// Rectangular concrete and slatted oak coffee table with decorative ceramic tray, small lantern, and beverage glasses.
// Size: 1.20m (W, X) x 0.40m (H, Y) x 0.70m (D, Z)
// Origin at base y = 0, centered footprint on XZ.
import * as THREE from 'three';

function mulberry32(a) {
  return function () {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildCoffeeTable(T = THREE, opts = {}) {
  const seed = opts.seed ?? 42;
  const variant = opts.variant ?? 0;
  const rng = mulberry32(seed + variant * 1013);

  const group = new T.Group();
  group.name = 'CoffeeTable';

  // --- Materials ---
  // Concrete legs / end supports
  const concreteMat = new T.MeshStandardMaterial({
    color: 0x938f87,
    roughness: 0.88,
    metalness: 0.05,
  });

  // Slatted oak wood
  const woodMat = new T.MeshStandardMaterial({
    color: 0xb07d4c,
    roughness: 0.65,
    metalness: 0.02,
  });

  // Dark steel support rails / frame
  const steelMat = new T.MeshStandardMaterial({
    color: 0x2b2927,
    roughness: 0.5,
    metalness: 0.8,
  });

  // Ceramic tray
  const ceramicMat = new T.MeshStandardMaterial({
    color: 0xe3dfd5,
    roughness: 0.35,
    metalness: 0.05,
  });

  // Lantern metal (dark bronze)
  const lanternMetalMat = new T.MeshStandardMaterial({
    color: 0x242220,
    roughness: 0.35,
    metalness: 0.85,
  });

  // Lantern glass
  const glassMat = new T.MeshStandardMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.35,
    roughness: 0.1,
    metalness: 0.1,
  });

  // Candle wax
  const candleWaxMat = new T.MeshStandardMaterial({
    color: 0xf5eedc,
    roughness: 0.7,
  });

  // Candle flame / light core
  const flameMat = new T.MeshStandardMaterial({
    color: 0xffaa33,
    emissive: 0xff7711,
    emissiveIntensity: 1.8,
    roughness: 0.3,
  });

  // Beverage glass & drink materials
  const drinkGlassMat = new T.MeshStandardMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.45,
    roughness: 0.08,
    metalness: 0.1,
  });

  const amberDrinkMat = new T.MeshStandardMaterial({
    color: 0xd9822b,
    transparent: true,
    opacity: 0.85,
    roughness: 0.2,
  });

  const waterDrinkMat = new T.MeshStandardMaterial({
    color: 0x6bb3c9,
    transparent: true,
    opacity: 0.7,
    roughness: 0.15,
  });

  // Book covers
  const bookMat1 = new T.MeshStandardMaterial({ color: 0x964332, roughness: 0.6 });
  const bookMat2 = new T.MeshStandardMaterial({ color: 0x3d4a54, roughness: 0.6 });
  const pageMat = new T.MeshStandardMaterial({ color: 0xeee6d8, roughness: 0.8 });

  // Succulent plant
  const potMat = new T.MeshStandardMaterial({ color: 0xded5c5, roughness: 0.5 });
  const plantMat = new T.MeshStandardMaterial({ color: 0x4a7a44, roughness: 0.7 });

  // ==========================================
  // 1. Table Structure: Dimensions 1.20 x 0.30 x 0.70
  // ==========================================
  // Concrete end slab legs (Left & Right)
  const legW = 0.09;
  const legH = 0.30;
  const legD = 0.70;
  const legGeo = new T.BoxGeometry(legW, legH, legD);

  const leftLeg = new T.Mesh(legGeo, concreteMat);
  leftLeg.position.set(-0.60 + legW / 2, legH / 2, 0);
  leftLeg.castShadow = leftLeg.receiveShadow = true;
  group.add(leftLeg);

  const rightLeg = new T.Mesh(legGeo, concreteMat);
  rightLeg.position.set(0.60 - legW / 2, legH / 2, 0);
  rightLeg.castShadow = rightLeg.receiveShadow = true;
  group.add(rightLeg);

  // Recessed concrete base plinth / bottom runners for solid grounding
  const plinthGeo = new T.BoxGeometry(1.02, 0.04, 0.50);
  const plinth = new T.Mesh(plinthGeo, concreteMat);
  plinth.position.set(0, 0.02, 0);
  plinth.castShadow = plinth.receiveShadow = true;
  group.add(plinth);

  // Black steel support rails underneath the wooden slats
  const railGeoX = new T.BoxGeometry(1.04, 0.025, 0.03);
  const frontRail = new T.Mesh(railGeoX, steelMat);
  frontRail.position.set(0, 0.27, 0.28);
  frontRail.castShadow = frontRail.receiveShadow = true;
  group.add(frontRail);

  const backRail = new T.Mesh(railGeoX, steelMat);
  backRail.position.set(0, 0.27, -0.28);
  backRail.castShadow = backRail.receiveShadow = true;
  group.add(backRail);

  const centerRail = new T.Mesh(railGeoX, steelMat);
  centerRail.position.set(0, 0.27, 0);
  centerRail.castShadow = centerRail.receiveShadow = true;
  group.add(centerRail);

  // Wooden Slatted Top
  // Span between inner face of left leg (-0.51) and inner face of right leg (+0.51)
  // 8 longitudinal slats with small gaps
  const slatLength = 1.02;
  const slatThickness = 0.025;
  const numSlats = 8;
  const slatWidth = 0.07;
  const slatSpacing = (0.66 - numSlats * slatWidth) / (numSlats - 1); // evenly distributed in Z
  const slatGeo = new T.BoxGeometry(slatLength, slatThickness, slatWidth);

  const startZ = -0.33 + slatWidth / 2;
  for (let i = 0; i < numSlats; i++) {
    const slat = new T.Mesh(slatGeo, woodMat);
    const zPos = startZ + i * (slatWidth + slatSpacing);
    slat.position.set(0, 0.285 + slatThickness / 2, zPos);
    slat.castShadow = slat.receiveShadow = true;
    group.add(slat);
  }

  // Top surface level is y = 0.30

  // ==========================================
  // 2. Decorative Ceramic Tray (X: ~ +0.18, Z: -0.02)
  // ==========================================
  const trayGroup = new T.Group();
  trayGroup.position.set(0.18, 0.30, -0.02);

  const trayBottomGeo = new T.BoxGeometry(0.38, 0.015, 0.28);
  const trayBottom = new T.Mesh(trayBottomGeo, ceramicMat);
  trayBottom.position.y = 0.0075;
  trayBottom.castShadow = trayBottom.receiveShadow = true;
  trayGroup.add(trayBottom);

  // Tray rims
  const rimLGeo = new T.BoxGeometry(0.015, 0.025, 0.28);
  const rimLeft = new T.Mesh(rimLGeo, ceramicMat);
  rimLeft.position.set(-0.19 + 0.0075, 0.015, 0);
  rimLeft.castShadow = true;
  trayGroup.add(rimLeft);

  const rimRight = new T.Mesh(rimLGeo, ceramicMat);
  rimRight.position.set(0.19 - 0.0075, 0.015, 0);
  rimRight.castShadow = true;
  trayGroup.add(rimRight);

  const rimFBGeo = new T.BoxGeometry(0.35, 0.025, 0.015);
  const rimFront = new T.Mesh(rimFBGeo, ceramicMat);
  rimFront.position.set(0, 0.015, 0.14 - 0.0075);
  rimFront.castShadow = true;
  trayGroup.add(rimFront);

  const rimBack = new T.Mesh(rimFBGeo, ceramicMat);
  rimBack.position.set(0, 0.015, -0.14 + 0.0075);
  rimBack.castShadow = true;
  trayGroup.add(rimBack);

  // ==========================================
  // 3. Small Lantern (placed on the tray)
  // ==========================================
  // Total height of lantern: base(0.01) + body(0.065) + roof/cap(0.015) + ring(0.01) = ~0.10m
  // Tray surface y = 0.315 -> top of lantern reaches y ≈ 0.40m
  const lanternGroup = new T.Group();
  lanternGroup.position.set(0.08, 0.015, -0.04);

  // Lantern Base
  const lBaseGeo = new T.BoxGeometry(0.07, 0.01, 0.07);
  const lBase = new T.Mesh(lBaseGeo, lanternMetalMat);
  lBase.position.y = 0.005;
  lBase.castShadow = true;
  lanternGroup.add(lBase);

  // Glass housing
  const lGlassGeo = new T.BoxGeometry(0.06, 0.055, 0.06);
  const lGlass = new T.Mesh(lGlassGeo, glassMat);
  lGlass.position.y = 0.0375;
  lanternGroup.add(lGlass);

  // Metal corner struts
  const strutGeo = new T.CylinderGeometry(0.002, 0.002, 0.055, 6);
  const strutOffsets = [
    [-0.03, -0.03],
    [0.03, -0.03],
    [-0.03, 0.03],
    [0.03, 0.03],
  ];
  for (const [sx, sz] of strutOffsets) {
    const strut = new T.Mesh(strutGeo, lanternMetalMat);
    strut.position.set(sx, 0.0375, sz);
    strut.castShadow = true;
    lanternGroup.add(strut);
  }

  // Candle inside
  const candleGeo = new T.CylinderGeometry(0.012, 0.012, 0.025, 10);
  const candle = new T.Mesh(candleGeo, candleWaxMat);
  candle.position.y = 0.0225;
  lanternGroup.add(candle);

  // Flame
  const flameGeo = new T.ConeGeometry(0.004, 0.01, 8);
  const flame = new T.Mesh(flameGeo, flameMat);
  flame.position.y = 0.04;
  lanternGroup.add(flame);

  // Lantern Cap / Roof
  const lRoofGeo = new T.ConeGeometry(0.05, 0.018, 4);
  lRoofGeo.rotateY(Math.PI / 4);
  const lRoof = new T.Mesh(lRoofGeo, lanternMetalMat);
  lRoof.position.y = 0.074;
  lRoof.castShadow = true;
  lanternGroup.add(lRoof);

  // Top ring handle
  const ringGeo = new T.TorusGeometry(0.008, 0.002, 6, 12);
  ringGeo.rotateX(Math.PI / 2);
  const ring = new T.Mesh(ringGeo, lanternMetalMat);
  ring.position.y = 0.088;
  lanternGroup.add(ring);

  trayGroup.add(lanternGroup);

  // ==========================================
  // 4. Beverage Glasses (on tray)
  // ==========================================
  // Glass 1: Tumbler with amber iced drink
  const g1Group = new T.Group();
  g1Group.position.set(-0.08, 0.015, 0.04);

  const glass1OuterGeo = new T.CylinderGeometry(0.028, 0.024, 0.065, 14);
  const glass1Outer = new T.Mesh(glass1OuterGeo, drinkGlassMat);
  glass1Outer.position.y = 0.0325;
  g1Group.add(glass1Outer);

  const drink1Geo = new T.CylinderGeometry(0.025, 0.022, 0.045, 12);
  const drink1 = new T.Mesh(drink1Geo, amberDrinkMat);
  drink1.position.y = 0.025;
  g1Group.add(drink1);

  // Small ice cubes inside glass 1
  const iceGeo = new T.BoxGeometry(0.01, 0.01, 0.01);
  const iceMat = new T.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.6, roughness: 0.1 });
  const ice1 = new T.Mesh(iceGeo, iceMat);
  ice1.position.set(0.005, 0.04, 0.003);
  ice1.rotation.set(0.2, 0.4, 0.1);
  g1Group.add(ice1);

  trayGroup.add(g1Group);

  // Glass 2: Slim tumbler with water & citrus slice
  const g2Group = new T.Group();
  g2Group.position.set(-0.07, 0.015, -0.06);

  const glass2OuterGeo = new T.CylinderGeometry(0.024, 0.022, 0.07, 14);
  const glass2Outer = new T.Mesh(glass2OuterGeo, drinkGlassMat);
  glass2Outer.position.y = 0.035;
  g2Group.add(glass2Outer);

  const drink2Geo = new T.CylinderGeometry(0.021, 0.020, 0.05, 12);
  const drink2 = new T.Mesh(drink2Geo, waterDrinkMat);
  drink2.position.y = 0.028;
  g2Group.add(drink2);

  // Lime / lemon slice
  const limeGeo = new T.CylinderGeometry(0.012, 0.012, 0.002, 10);
  limeGeo.rotateZ(Math.PI / 3);
  const limeMat = new T.MeshStandardMaterial({ color: 0x9cb837, roughness: 0.5 });
  const lime = new T.Mesh(limeGeo, limeMat);
  lime.position.set(0.014, 0.06, 0.002);
  g2Group.add(lime);

  trayGroup.add(g2Group);
  group.add(trayGroup);

  // ==========================================
  // 5. Stacked Books & Potted Succulent (X: ~ -0.28, Z: 0.04)
  // ==========================================
  const decorGroup = new T.Group();
  decorGroup.position.set(-0.28, 0.30, 0.04);
  decorGroup.rotation.y = 0.12;

  // Bottom book
  const b1CoverGeo = new T.BoxGeometry(0.22, 0.022, 0.16);
  const b1Cover = new T.Mesh(b1CoverGeo, bookMat1);
  b1Cover.position.y = 0.011;
  b1Cover.castShadow = true;
  decorGroup.add(b1Cover);

  const b1PagesGeo = new T.BoxGeometry(0.21, 0.018, 0.15);
  const b1Pages = new T.Mesh(b1PagesGeo, pageMat);
  b1Pages.position.set(0.003, 0.011, 0);
  decorGroup.add(b1Pages);

  // Top book (slightly angled)
  const b2Group = new T.Group();
  b2Group.position.set(0.01, 0.022, -0.01);
  b2Group.rotation.y = -0.15;

  const b2CoverGeo = new T.BoxGeometry(0.19, 0.018, 0.14);
  const b2Cover = new T.Mesh(b2CoverGeo, bookMat2);
  b2Cover.position.y = 0.009;
  b2Cover.castShadow = true;
  b2Group.add(b2Cover);

  const b2PagesGeo = new T.BoxGeometry(0.18, 0.014, 0.13);
  const b2Pages = new T.Mesh(b2PagesGeo, pageMat);
  b2Pages.position.set(0.003, 0.009, 0);
  b2Group.add(b2Pages);

  // Mini succulent pot on top book
  const potGeo = new T.CylinderGeometry(0.03, 0.022, 0.038, 12);
  const pot = new T.Mesh(potGeo, potMat);
  pot.position.set(0.02, 0.037, 0.01);
  pot.castShadow = true;
  b2Group.add(pot);

  // Succulent rosette leaves
  const leafGeo = new T.ConeGeometry(0.012, 0.022, 5);
  const leafAngles = [0, (2 * Math.PI) / 5, (4 * Math.PI) / 5, (6 * Math.PI) / 5, (8 * Math.PI) / 5];
  for (let i = 0; i < leafAngles.length; i++) {
    const angle = leafAngles[i];
    const leaf = new T.Mesh(leafGeo, plantMat);
    leaf.position.set(
      0.02 + Math.cos(angle) * 0.012,
      0.054,
      0.01 + Math.sin(angle) * 0.012
    );
    leaf.rotation.set(Math.sin(angle) * 0.4, 0, -Math.cos(angle) * 0.4);
    b2Group.add(leaf);
  }
  const centerLeaf = new T.Mesh(leafGeo, plantMat);
  centerLeaf.position.set(0.02, 0.058, 0.01);
  b2Group.add(centerLeaf);

  decorGroup.add(b2Group);
  group.add(decorGroup);

  // Measured overall bounding box
  group.userData.size = [1.20, 0.40, 0.70];

  // Subtle tick animation for candle flame flicker
  group.userData.tick = (t, dt) => {
    const flicker = 1.6 + 0.3 * Math.sin(t * 7.5) + 0.15 * Math.sin(t * 13.7 + 1.2);
    flameMat.emissiveIntensity = flicker;
    flame.scale.set(1 + 0.08 * Math.sin(t * 9.0), 1 + 0.12 * Math.sin(t * 11.0), 1 + 0.08 * Math.cos(t * 8.0));
  };

  return group;
}
