/** Atlantic cove: displaced water and fracture-plane basalt in late afternoon. */
import * as THREE from 'three';
import { makeOceanSurface } from '../lib/ocean.js';
import { makeRock } from '../lib/rock.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
export const BOUNDS = { min: [-110, -4, -110], max: [110, 14, 90] };
export function heightAt() {
  return -3;
}
export async function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({
    mood: 'golden',
    azimuth: -120,
    elevation: 16,
    bounds: 42,
    intensity: 4.1,
    fill: 1.25,
    disc: false,
  });
  scene.add(rig.sun, rig.fill);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, turbidity: 3.4, rayleigh: 1.6, scale: 3500 });
  scene.fog = new THREE.FogExp2(0xbec8c7, 0.0022);
  const ocean = makeOceanSurface({
    width: 1800,
    depth: 1800,
    segments: 384,
    waveHeight: 1.65,
    wavelength: 31,
    windDirection: [0.35, 1],
    choppiness: 1,
    foam: 0.9,
    seed: 83,
    reflectionSize: 1024,
    waterColor: 0x07353e,
    shallowColor: 0x278779,
    shoreline: { direction: [0, -1], position: -22, width: 5 },
  });
  scene.add(ocean);
  const rocks = [
    [-13, -0.7, 13, 8, 4.5, 8],
    [15, -1.2, 8, 8, 5.3, 8],
    [21, -1.8, -3, 7, 4.8, 6],
    [-21, -1.6, -8, 12, 8, 11],
    [-26, -2, -14, 8, 11, 8],
    [-18, -1.3, 22, 7, 3.4, 7],
    [20, -1, 22, 10, 4, 9],
    [-8, -1, 25, 5, 2, 5],
  ];
  for (let i = 0; i < rocks.length; i++) {
    const [x, y, z, w, h, d] = rocks[i];
    const rock = makeRock({
      type: 'basalt',
      seed: 91 + i,
      size: [w, h, d],
      detail: 5,
      moisture: 0.75,
      weathering: 1.15,
      moss: 0.08,
    });
    rock.position.set(x, y, z);
    rock.rotation.y = i * 0.77;
    scene.add(rock);
  }
  // The beach actually intersects the mean sea level; swash remains on its wet edge.
  const g = new THREE.PlaneGeometry(220, 90, 80, 35);
  g.rotateX(-Math.PI / 2);
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i),
      z = p.getZ(i) + 52;
    p.setXYZ(i, x, (z - 22) * 0.115 + Math.sin(x * 0.14) * 0.16, z);
  }
  g.computeVertexNormals();
  const beach = new THREE.Mesh(
    g,
    new THREE.MeshStandardMaterial({ color: 0x85775d, roughness: 0.94 })
  );
  beach.name = 'WetBeach';
  beach.receiveShadow = true;
  scene.add(beach);
  return {
    scene,
    cameras: [
      { name: 'ocean', position: [5, 3.8, 21], lookAt: [-2, 0.2, -11], fov: 51 },
      { name: 'wave_detail', position: [7, 2.0, 8], lookAt: [-4, 0.1, -9], fov: 44 },
    ],
    update(t, dt) {
      ocean.userData.update(t, dt);
    },
  };
}
