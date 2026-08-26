# scene_threejs — authoring contract

You write a **multi-file three.js scene in raw ESM + GLSL**. The harness owns the
page, the renderer, cameras rigs, rendering, probes and judging. Your code never
imports anything but `three`, `three/addons/*` and your own relative files.

## Frame & units
Y is **up**, +Z is the **front**, units are **meters**. Ground is around y = 0
(your `heightAt(x, z)` decides the exact surface). A person is 1.7 m; a door 2.1 m;
a car 4.5 m long. Authored bounds are declared by the plan (`bounds`).

## Entry: `src/scene.js`
```js
import * as THREE from 'three';
export function createScene({ THREE, renderer, loaders }) {   // may be async
  const scene = new THREE.Scene();
  // ... build env + zones, add them to scene ...
  return {
    scene,
    cameras: [ { name: 'overview', position: [26, 14, 34], lookAt: [0, 1.5, 0], fov: 50 } ],  // 1-6
    update(t, dt) { /* animate: t seconds since start, dt step */ },
  };
}
```
* The scene **owns its lights, sky/env and fog** (`scene.fog`, `scene.background`).
  No lights → MeshStandardMaterial renders black.
* `cameras`: 1–6 authored shots. Eye ≥ 0.5 m from any surface, above ground,
  looking at real content; name them by what they show (`harbour_low`, `overview`).
* `update(t, dt)`: all animation happens here (the host steps it deterministically).
  **Never** call `requestAnimationFrame`, create a `WebGLRenderer`, or call
  `renderer.render` yourself. `renderer` is passed only for capability checks.
* `loaders.gltf` is a `GLTFLoader`; `loaders.texture` a `TextureLoader`. Assets built
  with Blender live in `public/assets/<name>.glb` and are loaded with
  `await loaders.gltf.loadAsync('/assets/<name>.glb')` (make `createScene` async).

## Files
| path | export | role |
|---|---|---|
| `src/scene.js` | `createScene(ctx)` | entry; imports env + zones; returns `{scene, cameras, update}` |
| `src/env.js` | `buildEnv(ctx) → {ground, sky, sun, ...}`, `heightAt(x, z)` | ground/terrain, sky, sun + hemisphere light, fog |
| `src/zones/<snake>.js` | `build(ctx) → THREE.Group` | one zone = one **named** group (`group.name = 'PascalCase'`), animated via `group.userData.update(t, dt)` |
| `src/assets/<snake>.js` | `build<Pascal>(THREE) → THREE.Group` | reusable asset factories (origin at the base, +Y up); optionally `<asset>Parts(THREE)` for instancing |
| `src/shaders/<name>.js` | GLSL strings / material factories | `ShaderMaterial` or `onBeforeCompile` patches — raw GLSL |
| `src/fx/<name>.js` | effect helpers | particles, `three/addons/postprocessing/*` |

`ctx` passed to env/zones: `{ THREE, scene, renderer, loaders, env, heightAt }`
(you build it in `scene.js`; keep zones independent of each other).

## GLSL rules (the ones that fail silently)
* `#include <chunk>` **alone on its line**. No `#version`, no `precision` lines
  (three prepends them). Use `gl_FragColor` (no `glslVersion`), or set
  `glslVersion: THREE.GLSL3` and write your own `out vec4 fragColor` — never both.
* Every uniform you read must be declared in the GLSL **and** bound in JS
  (`uniforms: { uTime: { value: 0 } }`, set `.value` in `update`).
* ShaderMaterial gets no lights and no fog unless you add them: for fog,
  `fog: true`, `THREE.UniformsUtils.merge([THREE.UniformsLib.fog, {...}])` and the
  `fog_pars_vertex / fog_vertex / fog_pars_fragment / fog_fragment` chunks.
* `onBeforeCompile` patches must keep the chunk they extend:
  `shader.fragmentShader = shader.fragmentShader.replace('#include <output_fragment>', '#include <output_fragment>\n  gl_FragColor.rgb += glow;')`.
* Do not redeclare `position / normal / uv / projectionMatrix / modelViewMatrix / cameraPosition`.

## Performance & hygiene
* Repeats → `InstancedMesh` (trees, posts, windows). Budget ≤ 3 M triangles total.
* Seed your own PRNG (no `Math.random()`) so every render is reproducible.
* `castShadow/receiveShadow` on meshes that matter; one shadow-casting sun.
* Name things: zone groups PascalCase; important meshes too (the census/judge use names).
* No network, no CDN, no DOM besides `document.createElement('canvas')` for procedural textures.

## Harness tools you can call
`build` (probe + shader preflight), `check_placement` (per placed asset: floating / sunken /
unsupported / interpenetration with "lower X by 0.23 m onto Terrain" hints; escape hatch for a
deliberately airborne thing: `obj.userData.placement = 'free'`), `scene_probe` (census: meshes,
tris, lights, zone bboxes, overlaps), `shader_probe` (does your shader actually change the frame?),
`render_views` / `render_sheet` (authored + overview cameras at t = 0 and 1.5 s).
