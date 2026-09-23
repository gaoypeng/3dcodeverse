/** Mountain lake under a drifting, self-shadowed cumulus field. */
import * as THREE from 'three';
import { makeCloudVolume } from '../lib/cloudvolume.js';
import { makeOcean } from '../lib/water.js';
import { makeRock } from '../lib/rock.js';
import { makeMeadow } from '../lib/meadow.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { fbm2 } from '../lib/noise.js';

export const BOUNDS = { min: [-90, -5, -120], max: [90, 35, 90] };
export function heightAt(x, z) {
  const bank = .095 * z + .015 * x;
  return -3 + Math.max(0, bank) + fbm2(x * .07, z * .07, { seed: 27 }) * 2;
}

export function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({ mood: 'day', azimuth: -135, elevation: 32, bounds: 70, disc: false });
  scene.add(rig.sun, rig.fill);
  scene.environment = rig.envTex;
  scene.fog = new THREE.FogExp2(0xb2cad8, .00075);
  makeSky(scene, { rig, turbidity: 2.3, rayleigh: 1.45, scale: 8000 });

  const cloudOptions = [
    { size: [980, 660, 750], position: [-220, 700, -1900], seed: 29, coverage: .64 },
    { size: [1200, 520, 680], position: [900, 560, -2900], seed: 54, coverage: .61 },
    { size: [1300, 390, 680], position: [-1450, 540, -3200], seed: 87, coverage: .66 },
  ];
  const clouds = cloudOptions.map(({ position, ...options }) => {
    const cloud = makeCloudVolume({
      ...options, density: .07, quality: 'high', wind: [8, 1.3],
      sunDirection: rig.sunDir, sunColor: 0xfff7ea,
      skyColor: 0xacbfd8, sunIntensity: 1.55,
    });
    cloud.position.fromArray(position);
    scene.add(cloud);
    return cloud;
  });

  const lake = makeOcean(6000, 6000, {
    sunDir: rig.sunDir, waterColor: 0x153d44, distortionScale: .45,
    glitter: .3, rttSize: 1024, size: .65,
  });
  scene.add(lake);

  // Two staggered ridges supply a distance reference for the cloud scale.
  for (let layer = 0; layer < 2; layer++) {
    const geometry = new THREE.PlaneGeometry(4200, 850, 128, 40);
    geometry.rotateX(-Math.PI / 2);
    const position = geometry.attributes.position;
    for (let i = 0; i < position.count; i++) {
      const x = position.getX(i), z = position.getZ(i);
      const spine = Math.exp(-Math.pow((z + 80) / 270, 2));
      const ridge = 130 + 180 * fbm2(x * .0015, layer * 10, { seed: 13 + layer })
        + 70 * Math.sin(x * .0028 + layer * 2.1);
      position.setY(i, Math.max(-8, ridge * spine
        + fbm2(x * .011, z * .011, { seed: 11 + layer, octaves: 4 }) * 60));
    }
    geometry.computeVertexNormals();
    const ridge = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
      color: layer ? 0x566661 : 0x4e6155, roughness: .96,
    }));
    ridge.position.set(layer * 180, -6, -1150 - layer * 620);
    ridge.receiveShadow = true;
    scene.add(ridge);
  }

  const bankGeometry = new THREE.PlaneGeometry(180, 150, 90, 75);
  bankGeometry.rotateX(-Math.PI / 2);
  const bankPosition = bankGeometry.attributes.position;
  for (let i = 0; i < bankPosition.count; i++) {
    bankPosition.setY(i, heightAt(bankPosition.getX(i), bankPosition.getZ(i)));
  }
  bankGeometry.computeVertexNormals();
  const bank = new THREE.Mesh(bankGeometry, new THREE.MeshStandardMaterial({ color: 0x4d5140, roughness: .98 }));
  bank.receiveShadow = true;
  scene.add(bank);
  const grass = makeMeadow({
    size: [160, 130], density: 8, maxBlades: 70000, height: .34,
    seed: 63, color: 0x596b35, dry: .15, ground: false,
    heightAt, mask: (x, z) => THREE.MathUtils.smoothstep(heightAt(x, z), .4, 2),
    wind: { dir: [1, .2], strength: .9, speed: 1 / 1.2 },
  });
  scene.add(grass);
  for (let i = 0; i < 17; i++) {
    const x = -48 + i * 5.4;
    const z = 26 + Math.sin(i * 1.7) * 3;
    const rock = makeRock({
      seed: 90 + i, type: 'granite', size: [3.1 + i % 3, 1.8 + i % 2, 2.3 + i % 3],
      color: 0x767970, moisture: .75, moss: .12, burial: .16,
    });
    rock.position.set(x, heightAt(x, z) - .4, z);
    rock.rotation.y = i * .87;
    scene.add(rock);
  }
  return {
    scene,
    cameras: [
      { name: 'lake', position: [24, 8.5, 60], lookAt: [-75, 165, -950], fov: 53 },
      { name: 'clouds', position: [-200, 220, -600], lookAt: [-220, 670, -1900], fov: 46 },
      { name: 'inside', position: [-220, 650, -1820], lookAt: [200, 700, -1920], fov: 64 },
    ],
    update(t, dt) {
      clouds.forEach((cloud) => cloud.userData.update(t));
      lake.userData.update(t);
      grass.userData.update(t, dt);
    },
  };
}
