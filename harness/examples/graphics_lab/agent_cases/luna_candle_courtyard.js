import * as THREE from 'three';
import { makeCandle, makeFire } from '../lib/fire.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { weatheredWood, cobble, travertine, paintedIron } from '../lib/materials.js';
import { mulberry32 } from '../lib/noise.js';

function box(name, size, material, position, parent, cast = true) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
  mesh.name = name; mesh.position.set(...position); mesh.castShadow = cast; mesh.receiveShadow = true;
  parent.add(mesh); return mesh;
}

export function createScene({ THREE: T }) {
  const scene = new T.Scene();
  scene.background = new T.Color(0x22263a);
  scene.fog = new T.Fog(0x373347, 17, 48);
  const rig = sunRig({ mood: 'golden', azimuth: 232, elevation: 7, bounds: 14,
    sunColor: 0xffb27b, fillSky: 0x8b8fc0, fillGround: 0x62504f, intensity: 1.8 });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, exposure: 0.25, contrast: 1.2, scale: 120 });

  const courtyard = new T.Group(); courtyard.name = 'CandleCourtyard'; scene.add(courtyard);
  const stone = cobble({ color: 0x77716b, scale: 9, repeat: 1.4, variant: 0.52 });
  const warmStone = travertine({ color: 0x9a8671, scale: 5, repeat: 1.4, variant: 0.42 });
  const wood = weatheredWood({ color: 0x60442f, scale: 7, repeat: 1.1, variant: 0.38 });
  const iron = paintedIron({ color: 0x343332, scale: 9, variant: 0.4 });

  // A shallow paved court with a broken, individually articulated perimeter.
  box('CourtyardPaving', [12, 0.22, 10], stone, [0, -0.11, 0], courtyard);
  const rng = mulberry32(812);
  for (let i = 0; i < 34; i++) {
    const x = -5.5 + (i % 12) * 0.98 + (rng() - 0.5) * 0.1;
    const z = -4.6 + Math.floor(i / 12) * 1.0 + (rng() - 0.5) * 0.1;
    box(`PavingJointStone${i}`, [0.91, 0.025, 0.92], i % 4 ? warmStone : stone,
      [x, 0.013, z], courtyard, false);
  }
  // Rear and side walls leave the approach open; the irregular cap stones soften the silhouette.
  box('RearGardenWall', [12, 2.35, 0.48], warmStone, [0, 1.17, -5.15], courtyard);
  box('LeftGardenWall', [0.48, 2.0, 8.7], stone, [-5.85, 1, -0.35], courtyard);
  box('RightGardenWall', [0.48, 1.75, 8.7], stone, [5.85, 0.875, -0.35], courtyard);
  for (let i = 0; i < 17; i++) {
    const x = -5.65 + i * 0.7;
    box(`WallCoping${i}`, [0.68, 0.18, 0.58], i % 3 ? warmStone : stone,
      [x, 2.38 + Math.sin(i * 2.1) * 0.035, -5.12], courtyard);
  }
  // A low timber table, thick top, pegged legs and visible end grain rails.
  const table = new T.Group(); table.name = 'WeatheredOakTable'; table.position.set(-0.45, 0, -0.1); courtyard.add(table);
  box('TableTop', [1.48, 0.105, 0.78], wood, [0, 0.77, 0], table);
  for (let i = 0; i < 5; i++) box(`TablePlank${i}`, [1.44, 0.008, 0.145], wood, [0, 0.828, -0.29 + i * 0.145], table, false);
  for (const x of [-0.59, 0.59]) for (const z of [-0.27, 0.27]) {
    box(`TrestleLeg${x}_${z}`, [0.095, 0.69, 0.095], wood, [x, 0.38, z], table);
  }
  box('TableLongApron', [1.18, 0.13, 0.08], wood, [0, 0.63, 0], table);
  // Modest candle grouping; candle geometry is authored to real wax dimensions.
  const candles = [];
  const candleSpecs = [
    [-0.34, -0.08, 0.125, 0.029, 0xe7d4ad],
    [-0.08, 0.03, 0.158, 0.031, 0xdac59a],
    [0.22, -0.04, 0.102, 0.027, 0xeee0c2],
    [0.42, 0.11, 0.135, 0.028, 0xe5d1a5],
  ];
  candleSpecs.forEach(([x, z, h, r, color], i) => {
    const candle = makeCandle({ radius: r, height: h, seed: 110 + i * 17, color, lit: true,
      flameHeight: 0.047 + (i % 2) * 0.004, quality: 'high', lightIntensity: 0.48 });
    candle.name = `BeeswaxCandle${i + 1}`; candle.position.set(x, 0.841, z); table.add(candle); candles.push(candle);
  });
  const tableLantern = new T.Group(); tableLantern.name = 'CandleTray'; tableLantern.position.set(0.02, 0.833, 0.1); table.add(tableLantern);
  const tray = new T.Mesh(new T.CylinderGeometry(0.47, 0.45, 0.008, 48), iron);
  tray.name = 'DarkIronTray'; tray.position.y = 0.004; tray.castShadow = true; tableLantern.add(tray);

  // Compact, waist-low iron bowl on a stone plinth: visible coals and one restrained flame.
  const bowl = new T.Group(); bowl.name = 'LowFireBowl'; bowl.position.set(2.25, 0, 0.75); courtyard.add(bowl);
  const plinth = new T.Mesh(new T.CylinderGeometry(0.36, 0.42, 0.28, 12), warmStone);
  plinth.name = 'BowlStoneFoot'; plinth.position.y = 0.14; plinth.castShadow = plinth.receiveShadow = true; bowl.add(plinth);
  const bowlMesh = new T.Mesh(new T.LatheGeometry([
    new T.Vector2(0.2, 0), new T.Vector2(0.39, 0.08), new T.Vector2(0.49, 0.33),
    new T.Vector2(0.46, 0.39), new T.Vector2(0.39, 0.36), new T.Vector2(0.34, 0.20),
  ], 48), iron);
  bowlMesh.name = 'HammeredBrazier'; bowlMesh.position.y = 0.28; bowlMesh.castShadow = bowlMesh.receiveShadow = true; bowl.add(bowlMesh);
  const coals = new T.Mesh(new T.CircleGeometry(0.34, 48), new T.MeshStandardMaterial({
    color: 0x472116, emissive: 0x6d2109, emissiveIntensity: 0.7, roughness: 1 }));
  coals.name = 'BankedCoals'; coals.rotation.x = -Math.PI / 2; coals.position.y = 0.57; bowl.add(coals);
  const fuelMat = new T.MeshStandardMaterial({ color: 0x251913, roughness: 0.96 });
  for (let i = 0; i < 3; i++) {
    const log = new T.Mesh(new T.CylinderGeometry(0.055, 0.067, 0.52, 9), fuelMat);
    log.name = `BrazierFuelLog${i + 1}`;
    log.position.set(Math.cos(i * Math.PI / 3) * 0.07, 0.595 + i * 0.035, Math.sin(i * Math.PI / 3) * 0.07);
    log.rotation.set(Math.PI / 2 + i * 0.36, i * 0.24, Math.PI / 2 + i * 0.16);
    log.castShadow = true; bowl.add(log);
  }
  const fire = makeFire({ radius: 0.23, height: 0.48, seed: 91, quality: 'high', style: 'campfire', lightIntensity: 2.1 });
  fire.name = 'BrazierFlame'; fire.position.y = 0.56; bowl.add(fire);

  // A worn bench and earthen planters anchor the opposite edge without competing with the table.
  const bench = new T.Group(); bench.name = 'GardenBench'; bench.position.set(0.15, 0, 2.55); courtyard.add(bench);
  box('BenchSeat', [1.65, 0.09, 0.39], wood, [0, 0.46, 0], bench);
  for (const x of [-0.62, 0.62]) {
    box(`BenchLeg${x}`, [0.10, 0.43, 0.32], warmStone, [x, 0.23, 0], bench);
    box(`BenchBackPost${x}`, [0.075, 0.92, 0.075], wood, [x, 0.87, -0.16], bench);
  }
  box('BenchBack', [1.55, 0.20, 0.09], wood, [0, 0.90, -0.16], bench);
  for (const x of [-4.6, 4.55]) {
    const pot = new T.Mesh(new T.CylinderGeometry(0.30, 0.23, 0.47, 12, 1, true),
      new T.MeshStandardMaterial({ color: 0x7a4d3c, roughness: 0.94, side: T.DoubleSide }));
    pot.name = 'TerracottaPlanter'; pot.position.set(x, 0.235, 3.55); pot.castShadow = true; courtyard.add(pot);
    const soil = new T.Mesh(new T.CircleGeometry(0.235, 20), new T.MeshStandardMaterial({ color: 0x33251a, roughness: 1 }));
    soil.name = 'PlanterSoil'; soil.rotation.x = -Math.PI / 2; soil.position.set(x, 0.47, 3.55); courtyard.add(soil);
    for (let j = 0; j < 5; j++) {
      const stem = new T.Mesh(new T.CylinderGeometry(0.008, 0.012, 0.42 + (j % 2) * 0.12, 5),
        new T.MeshStandardMaterial({ color: 0x52623a, roughness: 1 }));
      stem.name = `HerbStem${j}`; stem.position.set(x + (rng() - 0.5) * 0.25, 0.75, 3.55 + (rng() - 0.5) * 0.22); stem.rotation.z = (rng() - 0.5) * 0.4; courtyard.add(stem);
    }
  }
  const fill = new T.PointLight(0xffa34d, 7, 5.5, 2); fill.name = 'BrazierBounce'; fill.position.set(2.2, 1.05, 0.75); courtyard.add(fill);
  const candlesLight = new T.PointLight(0xffbc6a, 1.8, 2.7, 2); candlesLight.name = 'CandleClusterBounce'; candlesLight.position.set(-0.45, 1.43, -0.1); table.add(candlesLight);

  const movers = [...candles, fire];
  scene.userData.update = (t, dt) => movers.forEach((item) => item.userData.update?.(t, dt));
  return {
    scene,
    cameras: [
      { name: 'Establishing', position: [0.05, 1.58, 1.45], lookAt: [0.02, 1.04, 0.02], fov: 43 },
      { name: 'CandleDetail', position: [-2.1, 2.0, 1.55], lookAt: [-0.32, 1.02, -0.12], fov: 43 },
      { name: 'BrazierAndTable', position: [4.2, 2.0, 4.0], lookAt: [0.7, 0.8, 0.3], fov: 52 },
    ],
    update(t, dt) { scene.userData.update(t, dt); },
  };
}
