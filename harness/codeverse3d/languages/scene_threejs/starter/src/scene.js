// src/scene.js — the ONE entry the harness loads.
//
// CONTRACT (scene_threejs):
//   export function createScene({ THREE, renderer, loaders })
//     → { scene, cameras: [{ name, position: [x,y,z], lookAt: [x,y,z], fov }], update(t, dt) }
//   • Y is up, +Z is front, units are meters.  The scene owns its lights, sky/env and fog.
//   • cameras: 1-6 authored shots (eye ≥ 0.3 m from any surface, above ground).
//   • update(t, dt): t = seconds since start, dt = step; animate here (no requestAnimationFrame).
//   • loaders.gltf is a GLTFLoader: loaders.gltf.loadAsync('/assets/<name>.glb') for Blender-built assets.
//   • Imports allowed: 'three', 'three/addons/*', relative files.  No CDN, no network, no DOM access.
//   • Raw three.js + GLSL only: never import a helper SDK or any npm package.
//     ./lib/ is the ONE exception and is not an SDK: harness-owned effect modules
//     shipped INTO this workspace, already compiled and rendered on this renderer.
//     Import and call them (`import { makeGrass } from './lib/grass.js'`); do not
//     rewrite them — writes to src/lib/ are reverted.  Table: prompts effects_catalog.
import * as THREE from 'three';
import { buildEnv, heightAt, SUN_AZIMUTH_DEG } from './env.js';
import { build as buildMeadow } from './zones/meadow.js';
import { build as buildPondside } from './zones/pondside.js';

export function createScene({ THREE: T = THREE, renderer, loaders }) {
  const scene = new THREE.Scene();
  const ctx = { THREE, scene, renderer, loaders, heightAt, sunAzimuthDeg: SUN_AZIMUTH_DEG };

  const env = buildEnv(ctx);            // ground, sky, sun, hemi light, fog
  ctx.env = env;

  // Each zone returns ONE named THREE.Group (PascalCase = zone name in the census/judge).
  const zones = [buildMeadow(ctx), buildPondside(ctx)];
  for (const z of zones) scene.add(z);

  const cameras = [
    { name: 'overview', position: [26, 14, 34], lookAt: [0, 1.5, 0], fov: 50 },
    { name: 'pond_low', position: [-4.5, heightAt(-4.5, 13) + 1.4, 13], lookAt: [-8, 0.3, 4], fov: 45 },
    { name: 'windmill', position: [9, heightAt(9, 9) + 1.7, 9], lookAt: [12, 4.5, -2], fov: 50 },
  ];

  // Animation fan-out: env first (sun/sky), then zones.  Keep update cheap.
  function update(t, dt) {
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }

  return { scene, cameras, update };
}
