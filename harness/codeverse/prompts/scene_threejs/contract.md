# Scene (multi-file three.js + GLSL) authoring contract — track `scene`, language `scene_threejs`

## Files (ES modules, three r182; Y-up, +Z front, meters)
```
src/scene.js        export async function createScene({ THREE, renderer, loaders })
                      → { scene, cameras, update(t, dt) }
src/env.js          export const BOUNDS; export function heightAt(x, z); export function buildEnv(THREE, scene) → { update(t, dt) }
src/zones/<snake>.js   export function build<Zone>(THREE, ctx) → THREE.Group   (ctx = { heightAt, loaders, ... })
src/assets/<snake>.js  export function build<Asset>(THREE, opts = {}) → THREE.Group   (procedural, Y-up, on y = 0)
src/shaders/<snake>.js export function make<Name>Material(THREE, opts = {}) → THREE.ShaderMaterial | patched material
src/lib/*.js        HARNESS-OWNED effect library (44 modules) — import and call, never rewrite
public/assets/<snake>.glb   (optional) Blender-built assets — the assembled scene.js preloads each into ctx.assets['<snake>']; zones clone it (its clips play by themselves)
```
The harness serves the workspace root over http (`public/assets/` is mounted at
`/assets/`), imports `src/scene.js` in headless Chrome with the real `THREE`, a configured
`WebGLRenderer` (shadows on, ACES tone mapping, sRGB output; logarithmic depth may be on
or off — write shaders that work either way) and `loaders = { gltf, texture, cube,
manager }` (GLTFLoader etc.).  It awaits `createScene`, validates the return value, then
for each camera renders at t ∈ {0, 1.5} s (calling `update`) and takes probes (tris, draw
calls, console/shader errors, camera-inside-geometry, black/blown frames, content coverage,
fps).  Frame gate (`scene_frames`, ERROR on authored cameras): mean luminance < 0.12 or
> 35 % near-black pixels = too dark; > 20 % pure white = blown; establishing shot with < 20 %
content pixels (rest sky/ground) = content too small.  Dusk/night = coloured, never black
(cookbook: "Dusk / night lighting recipe").

## createScene return value
* `scene`: `THREE.Scene` with `scene.fog` set and a background colour or sky dome.
* `cameras`: 3–5 **plain objects** `{ name, position: [x, y, z], lookAt: [x, y, z], fov }`
  (the harness builds the PerspectiveCameras: aspect 16/9, near 0.1, far from bounds).
  Names PascalCase from the plan (`Establishing`, `HarbourMid`, `LanternDetail`); fov
  35–60; eye height ≈ 1.6 m for human views; never inside or within 0.5 m of geometry; the
  first camera is the establishing shot showing ≥ 70 % of the bounds.
* `update(t, dt)`: advance animation (t seconds since start, dt seconds).  MUST be cheap
  (no allocations, no traversals — cache lists of animated objects / materials at build
  time).  Deterministic: the same t always gives the same frame.

## Imports (only these resolve)
`three`, `three/addons/*` (GLTFLoader, BufferGeometryUtils, Sky, Water, EffectComposer…).
Relative imports between your own files, and `src/lib/*.js`.  No CDN, no other packages,
no `three/webgpu`, no `three/tsl`.

`src/lib/` is NOT a helper SDK and the "raw language only" law does not exclude it: it is
harness-owned source shipped into this workspace, every module compiled and rendered on
this renderer, and writes to it are reverted.  Reach for it before writing your own
version of the same effect — the want → call table ships with this brief.

The harness renders scene pictures THROUGH a post chain (GTAO + a selective emissive
bloom + a grade that is identity unless `scene.userData.grade` is set).  So emissives DO
bloom: author them at peak 1.5–4, not 20.  `--no-post` / `CV3D_POST=0` turns it off.

## Forbidden
`document.*` / `window.*` except `window.innerWidth` — never create canvases or DOM;
`fetch`/XHR (use `loaders.gltf`); `Math.random()` (use a seeded hash `rand(i)`);
`Date.now()`/`performance.now()` (use `t`); `requestAnimationFrame`; creating a renderer;
changing renderer settings; environment sniffing (`navigator.userAgent`, headless checks).

## Budget
≤ 2 M triangles, ≤ 200 draw calls (InstancedMesh for anything repeated > 5×), ≤ 40
materials, textures only procedural (DataTexture / CanvasTexture is NOT available), GLB
assets ≤ 5 MB each, `createScene` resolves in < 15 s, `update` < 4 ms.

## Naming
Zones are Groups named PascalCase (plan zone names); assets Groups PascalCase; every
animated object has a stable `.name`; ShaderMaterials `mat.name = 'Water'`.

## COMPLETE minimal example (verified in node: module evaluates, createScene resolves, cameras valid)
`src/env.js`
```js
import * as THREE from 'three';
import { sunRig } from './lib/environment.js';
export const BOUNDS = { min: [-40, 0, -40], max: [40, 25, 40] };
export function heightAt(x, z) {                 // gentle rolling ground, deterministic
  return 0.6 * Math.sin(x * 0.12) * Math.cos(z * 0.09) + 0.2 * Math.sin((x + z) * 0.31);
}
export function buildEnv(THREE, scene) {
  scene.background = new THREE.Color(0x9fc4e8);
  scene.fog = new THREE.Fog(0x9fc4e8, 40, 140);
  // ONE rig: key + hemisphere fill + the environment map metals read from + disc + shadows
  // fitted to BOUNDS; tint through sunColor / fillSky / fillGround, never a second sun
  const rig = sunRig({ mood: 'day', azimuth: 35, elevation: 48, bounds: 45 });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  const geo = new THREE.PlaneGeometry(80, 80, 80, 80); geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) pos.setY(i, heightAt(pos.getX(i), pos.getZ(i)));
  geo.computeVertexNormals();
  const ground = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x5e7a3a, roughness: 1 }));
  ground.name = 'Ground'; ground.receiveShadow = true; scene.add(ground);
  return { update(t, dt) {} };
}
```
`src/zones/grove.js`
```js
import * as THREE from 'three';
const rand = (i) => { const s = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return s - Math.floor(s); };
export function buildGrove(THREE, { heightAt }) {
  const zone = new THREE.Group(); zone.name = 'Grove';
  const N = 40;
  const trunk = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.12, 0.18, 2.4, 8),
                                        new THREE.MeshStandardMaterial({ color: 0x5a3b22, roughness: 0.9 }), N);
  const crown = new THREE.InstancedMesh(new THREE.ConeGeometry(1.4, 3.2, 9),
                                        new THREE.MeshStandardMaterial({ color: 0x2f6b2a, roughness: 0.8 }), N);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  for (let i = 0; i < N; i++) {
    const x = -18 + 36 * rand(i), z = -18 + 36 * rand(i + 100), y = heightAt(x, z);
    const k = 0.8 + 0.5 * rand(i + 200);
    p.set(x, y + 1.2 * k, z); s.set(k, k, k); m.compose(p, q, s); trunk.setMatrixAt(i, m);
    p.set(x, y + 2.4 * k + 1.4 * k, z); m.compose(p, q, s); crown.setMatrixAt(i, m);
  }
  trunk.castShadow = crown.castShadow = true;
  trunk.computeBoundingSphere(); crown.computeBoundingSphere();
  zone.add(trunk, crown);
  zone.userData.update = (t) => { crown.rotation.y = 0.0; };   // hook for per-zone motion
  return zone;
}
```
`src/scene.js`
```js
import * as THREE from 'three';
import { buildEnv, heightAt, BOUNDS } from './env.js';
import { buildGrove } from './zones/grove.js';
export async function createScene({ THREE, renderer, loaders }) {
  const scene = new THREE.Scene();
  const env = buildEnv(THREE, scene);
  const grove = buildGrove(THREE, { heightAt, loaders });
  scene.add(grove);
  const cameras = [                                   // plain objects; the harness builds the cameras
    { name: 'Establishing', position: [38, 14, 42], lookAt: [0, 2, 0], fov: 40 },
    { name: 'GroveWalk', position: [6, heightAt(6, 20) + 1.6, 20], lookAt: [0, 2, 0], fov: 55 },
  ];
  const movers = [grove];
  return { scene, cameras, update(t, dt) { env.update(t, dt); for (const m of movers) m.userData.update?.(t, dt); } };
}
```
