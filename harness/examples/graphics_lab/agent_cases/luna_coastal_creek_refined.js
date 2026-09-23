import { makeStream } from '../lib/stream.js';
import { makeMeadow } from '../lib/meadow.js';
import { makeSandTerrain } from '../lib/sand.js';
import { makeRock } from '../lib/rock.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { mulberry32 } from '../lib/noise.js';

export function createScene({ THREE: T }) {
  const scene = new T.Scene();
  scene.background = new T.Color(0xb8d4dc);
  scene.fog = new T.Fog(0xb8d4dc, 62, 145);
  const rig = sunRig({ mood: 'day', azimuth: 224, elevation: 34, bounds: 28,
    sunColor: 0xffedcf, fillSky: 0xb7d9ee, fillGround: 0x8d8767, intensity: 2.3 });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, exposure: 0.28, contrast: 1.04, scale: 170 });

  const land = new T.Group(); land.name = 'SandyCoastalCreek'; scene.add(land);
  const seed = 8314, rng = mulberry32(seed);
  const points = [
    [-1.15, 0.68, -13], [-0.4, 0.53, -8], [0.82, 0.34, -2],
    [0.5, 0.19, 4], [-0.85, 0.055, 10], [-0.3, 0.0, 14],
  ];
  const stream = makeStream({ name: 'ShallowTidalCreek', points, width: 1.25,
    widthVariation: 0.18, depth: 0.27, speed: 0.38, roughness: 0.6,
    segments: 256, widthSegments: 24, stoneCount: 24, seed: seed + 1,
    waterColor: 0x56877e, attenuationDistance: 0.72, bedColor: 0x947d5d,
    obstacles: [
      { u: 0.26, lateral: -0.43, radius: 0.13 },
      { u: 0.57, lateral: 0.43, radius: 0.16 },
      { u: 0.82, lateral: -0.38, radius: 0.12 },
    ] });
  land.add(stream);

  const sand = makeSandTerrain({ name: 'LowWindWorkedDunes', size: [40, 48], segments: 512,
    duneHeight: 0.20, duneSpacing: 10.5, windDirection: [0.94, 0.34],
    rippleSpacing: 0.24, detailStrength: 0.82, grains: 45, windSpeed: 0.35,
    seed, color: 0xb39a73 });
  sand.position.y = -0.12;
  const ground = sand.children.find((child) => child.isMesh);
  const pos = ground.geometry.attributes.position;
  const pathSamples = Array.from({ length: 701 }, (_, i) => {
    const u = i / 700, s = stream.userData.sample(u, 0, 0);
    return { u, x: s.position.x, z: s.position.z };
  });
  const nearest = (x, z) => {
    // The creek runs generally north to south, so a local segment search avoids
    // the incorrect straight-across assumption at its two broad bends.
    let lo = 0, hi = pathSamples.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (pathSamples[mid].z < z) lo = mid; else hi = mid;
    }
    let best = pathSamples[lo], bestD = Infinity;
    for (let i = Math.max(0, lo - 12); i <= Math.min(pathSamples.length - 1, hi + 12); i++) {
      const p = pathSamples[i], dx = x - p.x, dz = z - p.z, d = dx * dx + dz * dz;
      if (d < bestD) { bestD = d; best = p; }
    }
    const s = stream.userData.sample(best.u, 0, 0);
    const ax = s.tangent.z, az = -s.tangent.x;
    return { u: best.u, lateral: (x - best.x) * ax + (z - best.z) * az,
      distance: Math.sqrt(bestD), centerX: best.x, centerZ: best.z };
  };

  // Cut a real shallow channel into the terrain. The valley floor follows the
  // stream grade and its banks rise to the exact edges of the water ribbon.
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i), duneY = pos.getY(i) + sand.position.y;
    const path = nearest(x, z), width = stream.userData.sample(path.u, 0, 0).width;
    const half = width * 0.5;
    const edge = half;
    const d = Math.abs(path.lateral) / edge;
    const waterY = stream.userData.sample(path.u, 0, 0).position.y;
    const bedY = waterY - 0.27 * Math.pow(1 - Math.min(d, 1), 0.72) - 0.004;
    let y;
    if (d <= 1) {
      y = bedY;
    } else if (d < 1.95) {
      const t = T.MathUtils.smoothstep(d, 1, 1.95);
      y = T.MathUtils.lerp(waterY, Math.max(duneY, waterY + 0.15), t);
    } else if (d < 3.1) {
      const t = T.MathUtils.smoothstep(d, 1.95, 3.1);
      y = T.MathUtils.lerp(Math.max(duneY, waterY + 0.15), duneY, t);
    } else {
      y = duneY;
    }
    y += 0.012 * (1 - T.MathUtils.smoothstep(d, 1, 3.1));
    // Geometry vertices are local to the offset sand group; all channel
    // targets above were derived from the stream's world-space grade.
    pos.setY(i, y - sand.position.y);
  }
  pos.needsUpdate = true;
  ground.geometry.computeVertexNormals();
  ground.geometry.computeBoundingBox();
  ground.geometry.computeBoundingSphere();
  sand.userData.update(0);
  land.add(sand);

  const grass = makeMeadow({ name: 'SparseSaltGrass', size: [35, 40], height: 0.34,
    density: 205, maxBlades: 65000, seed: seed + 20,
    heightAt: (x, z) => sand.userData.sampleHeight(x, z),
    mask: (x, z) => {
      const path = nearest(x, z), d = Math.abs(path.lateral) / 0.625;
      const bank = T.MathUtils.smoothstep(d, 1.35, 2.4);
      const gaps = 0.54 + 0.46 * Math.sin(x * 0.36 + z * 0.22 + 1.3) * Math.sin(z * 0.31 - x * 0.19);
      return bank * Math.max(0.08, gaps);
    }, wind: { direction: [0.94, 0.34], strength: 0.34, speed: 0.7 },
    diversity: 0.8, dry: 0.26, ground: false });
  grass.position.y = sand.position.y;
  land.add(grass);

  // Low, separated sea-oat clumps break the grass line on the higher dunes.
  const oatMaterial = new T.MeshStandardMaterial({ color: 0x8b8c5a, roughness: 0.94, side: T.DoubleSide });
  for (let i = 0; i < 56; i++) {
    const z = -15 + rng() * 29, x = (rng() - 0.5) * 20;
    if (nearest(x, z).distance < 2.3) continue;
    const h = 0.23 + rng() * 0.27;
    const tuft = new T.Group(); tuft.name = `SeaOatClump_${i}`;
    tuft.position.set(x, sand.position.y + sand.userData.sampleHeight(x, z), z); land.add(tuft);
    for (let j = 0; j < 5; j++) {
      const blade = new T.Mesh(new T.PlaneGeometry(0.038, h * (0.7 + rng() * 0.35)), oatMaterial);
      blade.position.y = h * 0.43; blade.rotation.y = j * Math.PI / 5;
      blade.rotation.z = (rng() - 0.5) * 0.27; tuft.add(blade);
    }
  }

  // Small rounded stones sit on the damp edge; larger broken rocks stay above
  // the waterline and are visibly seated in the sloping sand.
  const rockSpecs = [
    [-1.03, -9.1, [0.58, 0.30, 0.47]], [1.12, -5.1, [0.62, 0.32, 0.48]],
    [-1.08, -1.4, [0.67, 0.34, 0.52]], [1.08, 2.4, [0.55, 0.29, 0.46]],
    [-1.16, 6.8, [0.6, 0.31, 0.47]], [1.12, 9.8, [0.73, 0.37, 0.59]],
    [2.9, -11.5, [0.92, 0.45, 0.78]], [-3.3, 5.1, [0.86, 0.44, 0.71]],
  ];
  rockSpecs.forEach(([x, z, size], i) => {
    const rock = makeRock({ type: i % 3 === 0 ? 'sandstone' : 'granite', size,
      seed: seed + 80 + i * 17, detail: 5, moisture: i < 6 ? 0.34 : 0.06, burial: 0.11 });
    rock.name = `TideWornBankRock_${i + 1}`;
    rock.position.set(x, sand.position.y + sand.userData.sampleHeight(x, z) - 0.005, z);
    rock.rotation.y = rng() * Math.PI * 2; land.add(rock);
  });
  for (let i = 0; i < 24; i++) {
    const u = 0.06 + rng() * 0.88, side = i % 2 ? 1 : -1;
    const p = stream.userData.sample(u, side, 0);
    const offset = 0.12 + rng() * 0.22;
    const x = p.position.x + p.tangent.z * side * offset;
    const z = p.position.z - p.tangent.x * side * offset;
    const sx = 0.18 + rng() * 0.20, sy = 0.10 + rng() * 0.10, sz = 0.15 + rng() * 0.21;
    const pebble = makeRock({ type: i % 4 === 0 ? 'sandstone' : 'granite', size: [sx, sy, sz],
      seed: seed + 300 + i * 23, detail: 2, weathering: 0.72, moisture: 0.22, burial: 0.22 });
    pebble.name = `CreekEdgeStone_${i}`;
    pebble.position.set(x, sand.position.y + sand.userData.sampleHeight(x, z), z);
    pebble.rotation.y = rng() * Math.PI * 2; land.add(pebble);
  }

  scene.userData.update = (t, dt) => {
    stream.userData.update(t, dt);
    sand.userData.update(t, dt);
    grass.userData.update(t, dt);
  };
  return {
    scene,
    cameras: [
      { name: 'DuneOverview', position: [-5.8, 3.1, 7.2], lookAt: [-0.2, 0.24, -0.6], fov: 43 },
      { name: 'Waterline', position: [-2.8, 0.86, 2.7], lookAt: [0.05, 0.27, -2.1], fov: 47 },
      { name: 'BendDetail', position: [2.7, 0.95, -3.0], lookAt: [0.05, 0.26, -4.8], fov: 45 },
    ],
    update(t, dt) { scene.userData.update(t, dt); },
  };
}
