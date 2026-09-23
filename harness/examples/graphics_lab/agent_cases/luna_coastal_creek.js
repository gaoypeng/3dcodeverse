import { makeStream } from '../lib/stream.js';
import { makeMeadow } from '../lib/meadow.js';
import { makeSandTerrain } from '../lib/sand.js';
import { makeRock } from '../lib/rock.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { mulberry32 } from '../lib/noise.js';
import { cobble } from '../lib/materials.js';

export function createScene({ THREE: T }) {
  const scene = new T.Scene();
  scene.background = new T.Color(0xaad0e2);
  scene.fog = new T.Fog(0xb7d5df, 45, 120);
  const rig = sunRig({ mood: 'day', azimuth: 226, elevation: 39, bounds: 26,
    sunColor: 0xffedcf, fillSky: 0xb7d9ee, fillGround: 0x8d8767, intensity: 2.65 });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, exposure: 0.23, contrast: 1.08, scale: 160 });

  const land = new T.Group(); land.name = 'CoastalCreek'; scene.add(land);
  const seed = 4207, rng = mulberry32(seed);
  const points = [[0.25, 0.72, -12], [0.95, 0.57, -7], [0.45, 0.34, -1], [-0.72, 0.18, 5], [-0.36, 0.02, 12]];
  const sand = makeSandTerrain({ name: 'WindblownSand', size: [40, 44], segments: 192,
    duneHeight: 0.21, duneSpacing: 7.4, windDirection: [0.92, 0.38], rippleSpacing: 0.22,
    grains: 80, windSpeed: 0.55, seed, color: 0xa88b62 });
  sand.position.y = -0.12;
  const obstacles = [ { u: 0.22, lateral: -0.5, radius: 0.16 }, { u: 0.53, lateral: 0.45, radius: 0.18 }, { u: 0.78, lateral: -0.25, radius: 0.14 } ];
  const stream = makeStream({ name: 'CoastalCreekWater', points, width: 1.52, widthVariation: 0.13,
    depth: 0.25, speed: 0.72, roughness: 0.34, segments: 192, widthSegments: 16,
    stoneCount: 36, seed: seed + 1, waterColor: 0x5b9992, bedColor: 0x887d62,
    obstacles });
  stream.name = 'CoastalCreekWater'; land.add(stream);
  const suppliedBed = stream.getObjectByName('StreamBed');
  if (suppliedBed) suppliedBed.visible = false;
  const pathSamples = Array.from({ length: 481 }, (_, i) => {
    const u = i / 480, s = stream.userData.sample(u, 0, 0);
    return { u, x: s.position.x, z: s.position.z, width: s.width, tx: s.tangent.x, tz: s.tangent.z };
  });
  const nearestPath = (x, z) => {
    let lo = 0, hi = pathSamples.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (pathSamples[mid].z < z) lo = mid; else hi = mid; }
    const a = pathSamples[lo], b = pathSamples[hi];
    const f = T.MathUtils.clamp((z - a.z) / (b.z - a.z), 0, 1);
    const u = T.MathUtils.lerp(a.u, b.u, f), sample = stream.userData.sample(u, 0, 0);
    const best = { x: sample.position.x, z: sample.position.z, width: sample.width,
      tx: sample.tangent.x, tz: sample.tangent.z };
    const dx = x - best.x, dz = z - best.z;
    const acrossX = best.tz, acrossZ = -best.tx;
    return { u, width: best.width, lateral: dx * acrossX + dz * acrossZ,
      distance: Math.hypot(dx, dz), centerX: best.x, centerZ: best.z };
  };
  for (let i = 0; i < obstacles.length; i++) {
    const obstacle = obstacles[i];
    const original = stream.getObjectByName(`StreamObstacle_${i}`);
    if (original) original.visible = false;
    const sample = stream.userData.sample(obstacle.u, obstacle.lateral, 0);
    const rock = makeRock({ type: 'basalt', size: [obstacle.radius * 2.5, obstacle.radius * 1.4, obstacle.radius * 2.2],
      seed: seed + 180 + i * 19, detail: 6, moisture: 0.82 });
    rock.name = `FracturedWetWakeStone${i + 1}`;
    rock.position.copy(sample.position); rock.position.y -= obstacle.radius * 0.34;
    rock.rotation.y = rng() * Math.PI * 2; land.add(rock);
  }
  const groundMesh = sand.children.find((child) => child.isMesh);
  const terrain = groundMesh.geometry.attributes.position;
  for (let i = 0; i < terrain.count; i++) {
    const x = terrain.getX(i), z = terrain.getZ(i), base = terrain.getY(i);
    const path = nearestPath(x, z), d = Math.abs(path.lateral) / (path.width * 0.5);
    const waterY = stream.userData.sample(path.u, 0, 0).position.y;
    const bedY = waterY - 0.25 * (1 - 0.55 * Math.min(1, d) ** 2) + 0.025;
    const shelfY = Math.max(base, waterY + 0.055);
    let y;
    if (d <= 1.40) y = bedY;
    else if (d < 1.82) {
      const f = T.MathUtils.smoothstep(d, 1.40, 1.82);
      y = T.MathUtils.lerp(bedY, shelfY, f);
    } else if (d < 4.0) {
      const f = T.MathUtils.smoothstep(d, 1.72, 4.0);
      y = T.MathUtils.lerp(shelfY, base, f);
    } else y = base;
    terrain.setY(i, y);
  }
  terrain.needsUpdate = true; groundMesh.geometry.computeVertexNormals();
  sand.userData.update(0); land.add(sand);

  const plantRegions = [];
  const grass = makeMeadow({ name: 'ContinuousCoastalGrass', size: [34, 36], height: 0.52, density: 620,
    maxBlades: 170000, seed: seed + 20,
    heightAt: (x, z) => sand.userData.sampleHeight(x, z),
    mask: (x, z) => {
      const path = nearestPath(x, z), d = Math.abs(path.lateral) / (path.width * 0.5);
      const bank = T.MathUtils.smoothstep(d, 1.55, 2.15);
      const windGaps = 0.76 + 0.24 * Math.sin(x * 0.55 + z * 0.31 + 1.2) * Math.sin(z * 0.47 - x * 0.28);
      return bank * Math.max(0.28, windGaps);
    }, wind: { direction: [0.94, 0.34], strength: 0.48, speed: 1.05 }, ground: false });
  grass.position.y = sand.position.y; land.add(grass); plantRegions.push(grass);

  // Sparse dune-edge tufts create a broken transition into the distant shore.
  const tuftMat = new T.MeshStandardMaterial({ color: 0x8a9257, roughness: 0.92, side: T.DoubleSide });
  for (let i = 0; i < 44; i++) {
    const z = -12 + rng() * 24, x = (rng() - 0.5) * 18;
    if (Math.abs(nearestPath(x, z).lateral) < 2.1) continue;
    const h = 0.24 + rng() * 0.36;
    const tuft = new T.Group(); tuft.name = `SeaOatTuft${i}`; tuft.position.set(x, sand.position.y + sand.userData.sampleHeight(x, z), z); land.add(tuft);
    for (let j = 0; j < 5; j++) {
      const blade = new T.Mesh(new T.PlaneGeometry(0.045, h * (0.68 + rng() * 0.45)), tuftMat);
      blade.name = 'OatBlade'; blade.position.y = h * 0.45; blade.rotation.y = j * Math.PI / 5; blade.rotation.z = (rng() - 0.5) * 0.36; tuft.add(blade);
    }
  }

  // Fractured stones seat against the bank; none is allowed to cover the moving water ribbon.
  const rockSpecs = [
    [-1.65, -8.5, [0.82, 0.42, 0.66]], [2.2, -5.8, [0.72, 0.37, 0.58]],
    [-1.72, -2.4, [0.95, 0.5, 0.72]], [1.7, 0.9, [0.8, 0.43, 0.63]],
    [-1.85, 4.2, [0.72, 0.38, 0.56]], [1.25, 7.8, [0.92, 0.5, 0.76]],
    [-2.0, 9.8, [0.62, 0.31, 0.52]], [3.4, -9.6, [1.15, 0.56, 0.9]],
  ];
  rockSpecs.forEach(([x, z, size], i) => {
    const rock = makeRock({ type: i % 3 === 0 ? 'sandstone' : 'granite', size,
      seed: seed + 80 + i * 13, detail: 5, moisture: 0.12 });
    rock.name = `TideWornRock${i + 1}`;
    rock.position.set(x, sand.position.y + sand.userData.sampleHeight(x, z) - 0.025, z);
    rock.rotation.y = rng() * Math.PI * 2; land.add(rock);
  });

  // A few flattened cobbles and a weathered driftwood branch on the dry upper bank.
  const pebbleMat = cobble({ color: 0x77776e, scale: 12, variant: 0.46 });
  for (let i = 0; i < 16; i++) {
    const z = -10 + rng() * 20, side = i % 2 ? 1 : -1;
    const path = nearestPath(0, z);
    const x = path.centerX + side * (1.15 + rng() * 0.75);
    const pebble = new T.Mesh(new T.IcosahedronGeometry(0.12 + rng() * 0.12, 1), pebbleMat);
    pebble.name = `CreekPebble${i}`; pebble.scale.set(1.35, 0.48, 0.88);
    pebble.position.set(x, sand.position.y + sand.userData.sampleHeight(x, z) + 0.045, z);
    pebble.rotation.set(0, rng() * Math.PI, (rng() - 0.5) * 0.18); pebble.castShadow = pebble.receiveShadow = true; land.add(pebble);
  }
  const driftwood = new T.Mesh(new T.CylinderGeometry(0.075, 0.13, 2.15, 8),
    new T.MeshStandardMaterial({ color: 0x655540, roughness: 0.94 }));
  driftwood.name = 'SaltBleachedDriftwood'; driftwood.position.set(-5.0, sand.position.y + sand.userData.sampleHeight(-5, -6) + 0.10, -6); driftwood.rotation.z = Math.PI / 2 + 0.14; driftwood.rotation.x = 0.08; driftwood.castShadow = true; land.add(driftwood);

  scene.userData.update = (t, dt) => {
    stream.userData.update(t, dt);
    sand.userData.update(t, dt);
    for (const patch of plantRegions) patch.userData.update(t, dt);
  };
  return {
    scene,
    cameras: [
      { name: 'Establishing', position: [-3.8, 1.95, 4.3], lookAt: [-0.1, 0.49, -0.65], fov: 41 },
      { name: 'Creekside', position: [-4.8, 2.0, 4.7], lookAt: [0.25, 0.32, 0.4], fov: 47 },
      { name: 'WaterDetail', position: [3.7, 1.7, -2.2], lookAt: [0.1, 0.34, -3.2], fov: 44 },
    ],
    update(t, dt) { scene.userData.update(t, dt); },
  };
}
