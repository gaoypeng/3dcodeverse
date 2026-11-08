# The wiring (the interface, nothing else)

`src/scene.js` is the only caller of your modules; write it in this shape, the camera list from the
plan, and mark every zone root with `userData.c3d_zone` (placement and the judge find zones by it):
```js
import * as THREE from 'three';
import { buildEnv, heightAt } from './env.js';
import { build as buildQuay } from './zones/quay.js';            // one import per zone
export async function createScene({ THREE, renderer, loaders }) {
  const scene = new THREE.Scene();
  const assets = {};                                              // ctx.assets['<snake>'] ← each public/assets/<snake>.glb via loaders.gltf
  const ctx = { THREE, scene, renderer, loaders, heightAt, assets };
  const env = (await buildEnv(ctx)) || {}; ctx.env = env;         // ONE argument: ctx
  const zones = [await buildQuay(ctx)]; for (const g of zones) { g.userData.c3d_zone = g.name; scene.add(g); }
  const cameras = [{ name: 'Establishing', position: [0, 1.7, 30], lookAt: [0, 2, 0], fov: 50 }];  // the plan's list
  function update(t, dt) { if (env.update) env.update(t, dt); for (const z of zones) if (z.userData.update) z.userData.update(t, dt); }
  return { scene, cameras, update };
}
```

`src/env.js`
```js
import * as THREE from 'three';
export const SUN_AZIMUTH_DEG = 250;        // the plan's light, a plain number (0 = +X, 90 = +Z; read by regex)
export function heightAt(x, z) { return 0; }   // the brief's surface — any function of (x, z) that agrees with the ground mesh
export function buildEnv({ THREE, scene }) {
  // the brief's sky, light, ground (a mesh named 'Ground…' sampled from heightAt) and atmosphere:
  // scene.fog + scene.background set (no_fog / no_background), scene.environment an env texture
  return { update(t, dt) {} };               // the ONLY env hook scene.js calls
}
```

A zone module is `export function build(ctx) → THREE.Group` named after the zone, with its
animation on `group.userData.update`; an asset module is
`export function build<Asset>(THREE, opts = {}) → THREE.Group` with `group.userData.size`.

Shader text from files, inside async `createScene`:
```js
const text = new THREE.FileLoader(loaders.manager);
const [vertexShader, fragmentShader] = await Promise.all([
  text.loadAsync(new URL('./shaders/object.vert', import.meta.url).href),
  text.loadAsync(new URL('./shaders/object.frag', import.meta.url).href),
]);
const material = new THREE.ShaderMaterial({ vertexShader, fragmentShader });   // GLSL 3: glslVersion: THREE.GLSL3, no #version line
```
A hand-written shader loses Three's fog and depth chunks unless built with `makeShaderMaterial`
/ `patchStandard` from `lib/shader.js`.
