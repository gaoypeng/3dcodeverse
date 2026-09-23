import * as THREE from 'three';
import { makeSandTerrain } from '../lib/sand.js';
import { makeRock } from '../lib/rock.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';

/** Long stoss slopes, crisp lee crests and ripples lit across the wind. */
export function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({ mood: 'golden', bounds: 90, azimuth: 128, elevation: 16, intensity: 4.8, fill: 0.8,
    fillSky: 0x9eafd2, fillGround: 0x795333 });
  scene.add(rig.sun, rig.fill);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, turbidity: 3.1 });
  scene.fog = new THREE.FogExp2(0xc9b291, 0.0038);
  const sand = makeSandTerrain({ seed: 42, size: 240, segments: 512,
    duneHeight: 7.4, duneSpacing: 37, windDirection: [0.96, 0.28],
    rippleSpacing: 0.19, color: 0xc39b68, grains: 420, windSpeed: 1.2 });
  scene.add(sand);
  const h = sand.userData.sampleHeight;
  for (const [i, p] of [[-46, -55], [-57, -58], [-49, -66], [59, -73]].entries()) {
    const rock = makeRock({ seed: 118 + i, type: 'sandstone', size: [9 + i * 1.6, 8 + i, 8], detail: 6,
      color: 0x9b7253, burial: 0.20 });
    rock.position.set(p[0], h(...p), p[1]); rock.rotation.y = i * 0.74;
    scene.add(rock);
  }
  scene.userData.grade = { exposure: 0.98, contrast: 1.04, saturation: 1.02, warmth: 0.03 };
  const cameras = [
    { name: 'dunes', position: [28, h(28, 48) + 7.0, 48], lookAt: [-8, 3.4, -22], fov: 48 },
    { name: 'ripples', position: [3, h(3, 8) + 1.3, 8], lookAt: [-2, h(-2, -1), -1], fov: 48 },
    { name: 'crest', position: [-12, h(-12, 27) + 3.1, 27], lookAt: [-35, 2.4, -27], fov: 44 },
  ];
  return { scene, cameras, update(t, dt) { sand.userData.update(t, dt); },
    dispose() { sand.userData.dispose(); scene.traverse((o) => { if (o.userData.rockType) o.userData.dispose(); }); rig.envTex.dispose(); } };
}
