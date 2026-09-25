/** Moonlight over a basalt headland and a quiet, open sea. */
import * as THREE from 'three';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { makeClouds } from '../lib/clouds.js';
import { makeStars } from '../lib/celestial.js';
import { makeOceanSurface } from '../lib/ocean.js';
import { makeRock } from '../lib/rock.js';
import { fbm2, mulberry32 } from '../lib/noise.js';

export const BOUNDS = { min: [-500, -8, -780], max: [140, 55, 220] };

function shoreX(z) {
  return 8 + z * 0.18 + 5 * Math.sin(z * 0.016)
    - 12 * Math.exp(-(((z + 50) / 50) ** 2));
}

export function heightAt(x, z) {
  const inland = shoreX(z) - x;
  const coastalSlope = THREE.MathUtils.smoothstep(inland, -7, 28);
  const hills = 12 + 24 * Math.exp(-(((z + 230) / 150) ** 2))
    + 33 * Math.exp(-(((z + 570) / 170) ** 2));
  const rockRelief = fbm2(x * 0.055, z * 0.055, { seed: 53, octaves: 4 });
  return -3.2 + 7.4 * coastalSlope
    + hills * THREE.MathUtils.smoothstep(inland, 10, 150)
    + rockRelief * 3.1 * coastalSlope;
}

export function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({
    mood: 'night', azimuth: -72, elevation: 21, bounds: 95,
    intensity: 0.55, fill: 0.36, fillSky: 0x8293ab, fillGround: 0x121823,
    radius: 4000, disc: true,
  });
  // The general rig has a readability floor; this exposure calls for a
  // dimmer sky fill so moonlit faces separate from sheltered basalt.
  rig.fill.intensity = 0.36;
  scene.add(rig.sun, rig.fill, rig.sunDisc);
  scene.environment = rig.envTex;
  scene.environmentIntensity = 0.55;
  const sky = makeSky(scene, { rig, turbidity: 2.7, scale: 4500 });
  scene.fog = new THREE.FogExp2(0x253143, 0.00065);
  scene.userData.grade = { exposure: 0.95, contrast: 1.015, saturation: 0.78 };

  const stars = makeStars({
    seed: 18, count: 650, magnitude: 0.34, twinkle: 0.1, milkyWay: false,
  });
  scene.add(stars);
  const clouds = makeClouds({
    preset: 'night', seed: 19, count: 14, area: 2400, altitude: 190, spread: 70,
    sunDir: rig.lightDir, alpha: 0.6, wind: 1.5, rim: 0.22, haze: 0.14, quality: 'high',
  });
  clouds.rotation.y = 0.65;
  scene.add(clouds);

  const ocean = makeOceanSurface({
    seed: 83, width: 2400, depth: 2400, segments: 320,
    waveHeight: 0.32, wavelength: 22, windDirection: [0.35, 1],
    choppiness: 0.5, foam: 0.06, reflectionSize: 1024,
    waterColor: 0x081b23, shallowColor: 0x183339, sunDir: rig.lightDir,
  });
  scene.add(ocean);

  // A continuous coast extends beneath the water. Height and dressing share
  // one field, keeping the foreshore attached even between large outcrops.
  const terrainGeometry = new THREE.PlaneGeometry(650, 1000, 180, 240);
  terrainGeometry.rotateX(-Math.PI / 2);
  terrainGeometry.translate(-190, 0, -280);
  const positions = terrainGeometry.attributes.position;
  const colors = new Float32Array(positions.count * 3);
  const base = new THREE.Color(0x55534d), color = new THREE.Color();
  for (let i = 0; i < positions.count; i++) {
    const x = positions.getX(i), z = positions.getZ(i);
    positions.setY(i, heightAt(x, z));
    const variation = fbm2(x * 0.035, z * 0.035, { seed: 117, octaves: 3 });
    color.copy(base).multiplyScalar(0.88 + variation * 0.46).toArray(colors, i * 3);
  }
  terrainGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  terrainGeometry.computeVertexNormals();
  const terrainMaterial = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.94 });
  const terrain = new THREE.Mesh(terrainGeometry, terrainMaterial);
  terrain.name = 'CoastalHeadland';
  terrain.castShadow = terrain.receiveShadow = true;
  scene.add(terrain);

  const rocks = [], rng = mulberry32(271);
  const addRock = (x, z, size, seed) => {
    const rock = makeRock({
      type: 'basalt', seed, size, detail: 5, color: 0x555b60,
      moisture: 0.65, weathering: 1.2, moss: 0.02, burial: 0.13,
    });
    rock.position.set(x, heightAt(x, z) - 0.22, z);
    rock.rotation.y = rng() * Math.PI * 2;
    scene.add(rock); rocks.push(rock);
  };
  addRock(-7, 12, [13, 4.6, 12], 72);
  addRock(2, 23, [10, 3.2, 9], 18);
  addRock(-8, -5, [8, 3.8, 10], 84);
  addRock(-23, -32, [11, 5.8, 14], 46);
  for (let i = 0; i < 22; i++) {
    const z = -46 + rng() * 83, x = shoreX(z) - 1 - rng() * 16;
    const size = 0.7 + rng() ** 2 * 3.3;
    addRock(x, z, [size * 1.5, size * 0.62, size], 401 + i);
  }

  let disposed = false;
  return {
    scene,
    cameras: [
      { name: 'moonlit_coast', position: [8, 4.8, 23], lookAt: [0, 22, -115], fov: 46 },
      { name: 'waterline', position: [11, 2.2, 4], lookAt: [5, 17, -120], fov: 43 },
    ],
    update(t, dt) {
      stars.userData.update(t); clouds.userData.update(t); ocean.userData.update(t, dt);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const object of [stars, clouds, ocean, ...rocks]) object.userData.dispose();
      terrainGeometry.dispose(); terrainMaterial.dispose(); sky.dispose(); rig.dispose();
    },
  };
}
