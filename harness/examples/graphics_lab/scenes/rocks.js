import * as THREE from 'three';
import { makeRock, makeRockField } from '../lib/rock.js';
import { makeSandTerrain } from '../lib/sand.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { mulberry32 } from '../lib/noise.js';

/** A weathered outcrop: sedimentary ledges, mineral grains and damp talus. */
export function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({ mood: 'day', bounds: 24, azimuth: 138, elevation: 24, intensity: 5.2, fill: 0.7 });
  rig.sun.shadow.mapSize.set(4096, 4096);
  rig.sun.shadow.normalBias = 0.014;
  scene.add(rig.sun, rig.fill); scene.environment = rig.envTex;
  makeSky(scene, { rig, turbidity: 2.8 });
  scene.fog = new THREE.FogExp2(0xafbdc4, 0.007);
  const ground = makeSandTerrain({ seed: 61, size: 140, segments: 256, duneHeight: 0.30, duneSpacing: 13,
    color: 0x766c57, rippleSpacing: 0.10, detailStrength: 0.30 });
  scene.add(ground);
  const h = ground.userData.sampleHeight, rocks = [];
  const add = (opts, x, z, a = 0) => {
    const rock = makeRock(opts); rock.position.set(x, h(x, z), z); rock.rotation.y = a;
    scene.add(rock); rocks.push(rock); return rock;
  };
  add({ type: 'sandstone', seed: 19, size: [5.2, 3.8, 3.6], color: 0x947155, detail: 12, moisture: 0.15 }, -1.2, -1.6, 0.5);
  add({ type: 'sandstone', seed: 73, size: [3.0, 1.0, 2.0], color: 0x947155, detail: 10 }, -3.6, 0.1, -0.6);
  add({ type: 'granite', seed: 31, size: [2.8, 1.9, 2.3], detail: 10, moisture: 0.6, moss: 0.35 }, 2.0, 1.7, 0.3);
  add({ type: 'granite', seed: 52, size: [1.2, 0.85, 1.0], detail: 7, moisture: 0.55, moss: 0.6 }, 0.4, 3.1, 0.8);
  add({ type: 'basalt', seed: 84, size: [2.1, 1.8, 1.7], detail: 8, moisture: 0.28 }, 3.5, -2.2, 0.35);
  add({ type: 'basalt', seed: 81, size: [1.7, 0.9, 1.2], detail: 6, moisture: 0.2 }, 4.4, -1.7, 0.7);
  // Broken blocks accumulate at the parent outcrop. A second size tier and
  // smaller scree connect the hero stones to the substrate instead of leaving
  // unrelated samples evenly spaced across an empty plane.
  const rand = mulberry32(908);
  for (let i = 0; i < 23; i++) {
    const a = rand() * Math.PI * 2, r = 1.5 + rand() * 2.8;
    const x = -1.7 + Math.cos(a) * r, z = -1.1 + Math.sin(a) * r;
    if (x > 0.3 && z > 0.5) continue;
    const s = 0.42 + rand() * 1.0;
    add({ type: 'sandstone', seed: 801 + i, size: [s * 1.4, s * 0.65, s], detail: 5,
      color: 0x8b7053, weathering: 0.75, burial: 0.13 }, x, z, a);
  }
  const talus = makeRockField({ type: 'granite', seed: 35, radius: 7, count: 110, size: 0.3, detail: 2, moisture: 0.35, heightAt: h });
  scene.add(talus);
  for (let i = 0; i < 15; i++) add({ type: 'sandstone', seed: 210 + i,
    size: [9 + (i % 3) * 3, 3 + (i % 4), 8], color: 0x847b68, detail: 4, burial: 0.22 },
  -43 + i * 6, -23 - (i % 2) * 3, i * 0.7);
  scene.userData.grade = { exposure: 1, contrast: 1.04, saturation: 0.99 };
  return { scene, cameras: [
    { name: 'outcrop', position: [10.4, 5.2, 12.9], lookAt: [0.1, 1.5, -0.3], fov: 47 },
    { name: 'granite', position: [4.3, 2.8, 5.5], lookAt: [1.8, 0.8, 1.6], fov: 43 },
    { name: 'strata', position: [-4.8, 3.0, 4.0], lookAt: [-1.3, 1.8, -0.8], fov: 42 },
  ], update() {}, dispose() { ground.userData.dispose(); talus.userData.dispose(); rocks.forEach((r) => r.userData.dispose()); rig.envTex.dispose(); } };
}
