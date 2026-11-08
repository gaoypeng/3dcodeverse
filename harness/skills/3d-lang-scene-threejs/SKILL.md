---
name: 3d-lang-scene-threejs
description: "Use when writing, refining or repairing a scene in three.js (language scene_threejs): the module files and their exact exports (createScene, env heightAt/buildEnv/SUN_AZIMUTH_DEG, zone build, asset build<Name>), the Y-up metre frame, imports and the forbidden list, the harness-owned src/lib toolbox, the thresholds check-placement, scene-views (exposure, camera, motion) and scene-probe measure, naming, and the 3dcode loop."
license: Apache-2.0
compatibility: three r182 ESM in headless Chrome (WebGLRenderer, shadows, ACES, sRGB, a post chain with GTAO + selective bloom) driven by the 3dcode build; every authored camera rendered at t = 0 and t = 1.5 s.
metadata:
  verified: "2026-11-07"
---

# three.js scenes

You write RAW ESM modules: `import * as THREE from 'three'`, `three/addons/*`, your own
relative files and the harness-owned `src/lib/*.js` — nothing else.  The build owns the
renderer, the render loop and the export; you build the scene graph and the cameras.

## Files (Y up, +Z front, metres)
```
src/scene.js        export async function createScene({ THREE, renderer, loaders }) → { scene, cameras, update(t, dt) }
src/env.js          export function heightAt(x, z); export function buildEnv(ctx) → { update(t, dt) }; export const SUN_AZIMUTH_DEG
src/zones/<snake>.js   export function build(ctx) → THREE.Group   (ctx = { THREE, scene, renderer, loaders, heightAt, env, assets })
src/assets/<snake>.js  export function build<Asset>(THREE, opts = {}) → THREE.Group   (procedural; lowest point on y = 0)
src/shaders/<snake>.js export function make<Name>Material(THREE, opts = {}) → ShaderMaterial | patched material
src/lib/*.js        HARNESS-OWNED effect library (references/effects-catalog.md); import and call; edits are reverted
public/assets/<snake>.glb   optional Blender-built heroes; scene.js preloads each into ctx.assets['<snake>']
```
The build serves the workspace over http (`public/assets/` at `/assets/`), imports
`src/scene.js` in headless Chrome with the real `THREE`, a configured `WebGLRenderer` and
`loaders = { gltf, texture, cube, manager }`, awaits `createScene`, validates the return
value, renders every camera at t = 0 and t = 1.5 s (calling `update`) and probes (tris, draw
calls, console/shader errors, camera checks, exposure, content, the frame difference).  The
plan's `bounds` are what the census and placement gate measure against; the world a camera
sees extends past them as far as the brief needs.  `references/interface.md` has the wiring
of `scene.js` and the env module's shape.

## createScene
* `scene`: a `THREE.Scene`; `scene.fog` and `scene.background` set (unset = `no_fog` /
  `no_background`, ERROR); `scene.environment` an environment texture (metals and glass render
  near black without one).  Whatever a camera sees ends in something the brief justifies.
* `cameras`: a non-empty list of PLAIN objects `{ name, position: [x, y, z], lookAt: [x, y, z], fov }`
  (the build makes the PerspectiveCameras: aspect 16/9, near 0.1).  PascalCase names from the
  plan; `cameras[0]` is the establishing view (≥ 20 % content; others ≥ 10 %,
  `content_small`).  Eye ≥ 0.3 m above the ground under it (`camera_low` WARN; `camera_high`
  above 80 m), ≥ 0.5 m from every surface and not inside geometry (`camera_in_geometry`, ERROR,
  cap 0.50), at most 5 of its 9 sight rays ending within 1.5 m (`camera_blocked`, cap 0.50), the
  line of sight to `lookAt` not cut in its first half; below the terrain is `camera_underground`
  (cap 0.50) — compute y from `heightAt(x, z)`, never guess.
* `update(t, dt)`: cheap (no allocations, traversals or material creation — cache lists at
  build time) and deterministic (same t → same frame).  It fans out to `env.update` and every
  zone's `group.userData.update`; `tickEnv`, `userData.tick` and `build<Zone>(ctx)` are never
  called — a hook nobody calls stands still, and calling a missing hook throws.
* `src/env.js`: `heightAt(x, z)` is the surface every zone seats on — deterministic, cheap,
  `0` for a flat floor; every walkable surface (a path, a deck) is part of it, and it must agree
  with the ground mesh.  `SUN_AZIMUTH_DEG` is a plain numeric `export const` (read by regex to
  put the overview rig on the sun side).  Anything else it exports is a convention between it
  and the zones.

## Imports and sandbox
Only `three`, `three/addons/*`, relative files and `src/lib/*.js` resolve; no CDN, no other
package, no `three/webgpu` / `three/tsl`.  Shader text: `.vert` / `.frag` / `.glsl` files under
`src/shaders/`, loaded in async `createScene` with
`new THREE.FileLoader(loaders.manager).loadAsync(new URL('./shaders/x.frag', import.meta.url).href)`
— no bundler `?raw`, no `#include` of your own files (Three's `#include <chunk>` is its
built-ins only); never load or compile during `update`.  Forbidden: `document.*` / `window.*`
except `window.innerWidth` and `document.createElement('canvas')` for a procedural texture;
network, arbitrary `fetch` / XHR; `Math.random()` (`mulberry32(seed)` from `lib/noise.js`);
`Date.now()` / `performance.now()` (use `t`); `requestAnimationFrame`; creating a renderer or
changing its settings; environment sniffing; `EffectComposer` (nothing reads one).  Textures:
procedural (incl. DataTextures) or `loaders.texture` for files present in the workspace.

## What is measured
* **Placement** (`3dcode check-placement`; runs on every build): each direct child of a zone
  group is ONE placed object; from its lowest vertices the probe looks down for the nearest
  surface and up for one passing through it.  Gap under the foot > 0.05 m outdoors / 0.02 m
  indoors = `floating` (ERROR at 0.08 m, or more than two floaters); buried > 0.10 m = `sunken`
  (ERROR at 0.20 m and > 20 % of the height; buried, slope-following and water-level feet are
  exempt); a bbox touching nothing = `unsupported`; two boxes sharing > 10 % of the smaller =
  `interpenetration` (ERROR at 35 %).  A Group of several props is ONE box, their union, and
  every prop inside reads as interpenetrating it: one placed thing = one direct child; many of
  one thing = one `InstancedMesh` (a scattered instance inside a placed thing is that thing's
  interpenetration).  `obj.userData.placement = 'free'` for things meant to hang (WARN when
  > 25 % are tagged, ERROR past 50 %).  Four or more foot columns with at most one touching and
  a gap > 0.05 m is `cantilevered` (WARN).  A placed asset is measured against the plan's
  `approx_size_m`: WARN beyond 2.5 × (or under 1 / 2.5), ERROR beyond 4 × — never re-scale to
  fit or stretch a clone; a chimney or cable that must reach further is a separate object.
* **Bounds and density**: a planned content entirely outside the bounds + max(5 m, 10 % of the
  span) is `out_of_bounds` (WARN; scenery past them is never reported); a zone group missing or
  empty `zone_empty`; a planned asset not placed `missing_content`; a zone under half its
  layout's count an INFO `density_note`.
* **The world's extent** (exteriors): standing geometry ≥ 2 m tall reaches ≥ 1.25 × the bounds
  half-extent (`world_edge`, WARN); fog far ≥ 2 × the plan span, or for `FogExp2`
  1 / density ≥ span / 1.5 (`fog_short`); ≥ 8 same-size copies evenly on a ring is
  `stamped_ring`.
* **Frames** (`3dcode scene-views`, per authored camera): mean luminance < 0.12 or > 35 %
  near-black pixels = `dark_frame` (unless luminance std ≥ 0.12 with ≤ 35 % near-black: low-key
  but lit); > 20 % blown = `blown_frame`; > 85 % of the pixels in one band = `flat_frame`
  (ERROR above 92 %); plus the camera checks and `content_small` above.  Exposure is gated
  before anything is judged: a frame that fails scores nothing for what is in it.  Non-solid
  cards near a camera read as geometry to the raycast — `mesh.raycast = () => {};`.
* **Motion**: the t = 0 and t = 1.5 s frames differenced per camera; a camera MOVES when ≥ 0.4 %
  of its pixels change by more than 8/255 (or ≥ 0.08 % by more than 60/255).  No authored camera
  moving is `no_motion` (ERROR) and the judge's `nothing_moves`.  Sub-centimetre sway is
  invisible at 1024 × 576: foliage ±0.10–0.20 rad, particles ≥ 0.6 m per 1.5 s, water ≥ 3 cm or
  a scrolling normal have passed.
* **Hero** (`hero_unseen`): a `public/assets/*.glb` covering < 0.5 % of the frame in EVERY
  camera is an ERROR; < 2 % in the camera the plan names for it a WARN.  A hero is visible only
  if a zone clones `ctx.assets['<snake>']` (its pivot is at its base; clips play by themselves;
  `clone.userData.clipOffset = <s>` de-phases a copy).
* **Budget** (`3dcode scene-probe`): WARN above 5 000 draw calls, ERROR above 15 000;
  3 000 000 unique triangles.  `InstancedMesh` and merged static geometry are the cheap repeats.
* **Shaders**: a console or shader error in any module is a build finding; `3dcode
  shader-probe` compiles a material before it renders black.
* **Assets**: a procedural asset module is built in node when written; one instance ≤ 15 000
  triangles, lowest point y = 0, footprint centred, size the plan's (ERROR beyond 2.5 × or under
  0.4 × on any axis, and on a single-box blockout).  Set `group.userData.size = [w, h, d]`;
  optional `group.userData.update = (t, dt) => {}` which the placing zone caches and calls.
  `opts.seed` / `opts.variant` vary details deterministically; `variant` changes the SILHOUETTE.

## Naming
Zones are Groups named PascalCase (plan names); assets PascalCase; every animated object has a
stable `.name`; `mat.name = 'Water'` on ShaderMaterials; the ground mesh `Ground`; backdrop
geometry beyond the content `Sky…` (classified as sky, so it does not inflate the content box);
an interior's lid meshes `Ceiling…` / `RoomShell…` (the overview rig lifts them).

## Requirements that hold for every brief
* The brief's time of day and weather are hard constraints the judge compares every frame
  against; a night brief under a noon sky is the wrong scene whatever stands in it.  Flat fill
  (`AmbientLight`, a hemisphere with no key) turns a lit night into a grey wash: add light where
  the brief's sources are, give dark materials a low but non-zero albedo (albedos in 0.02–0.8
  linear; ACES at exposure 1.0).  Shade may stay dark but must carry colour.
* The environment is judged as heavily as the props (`brief_fidelity`, `lighting_atmosphere`,
  `flat_ground`, `diorama_look`): a default sky over default green ground is unfinished.
  Distant forms pre-tinted toward the fog render BRIGHTER than the terrain and read as cardboard
  (`placeholder_material`); natural colour, darkened, left to the fog, reads as haze.
* An interior (`interior: true`) must read enclosed from every camera — walls and a ceiling
  exist in every frame, lit from the sources the brief names (a closed room with no openings and
  no practicals renders black).  `roomShell` in `lib/environment.js` is one option.
* Shadows: `castShadow` on the light AND the meshes, `receiveShadow` on what they fall on; a
  shadow texel larger than ~0.05 m over the content reads as jagged (judged) — `sunRig({ bounds })`
  with the CONTENT radius fits frustum, map size and bias.
* Emissives bloom through the post chain (GTAO + selective bloom + an identity grade unless
  `scene.userData.grade` is set): author practicals at peak 1.5–4, not 20;
  `material.userData.bloom = false | true | <colour>` opts a material in or out.
* A sky-dome material needs `fog: false`, `side: THREE.BackSide`, `depthWrite: false` (and the
  tonemap / colorspace chunks `makeShaderMaterial` adds) or it renders black or fogged.
  `mergeGeometries` returns `null` on mismatched attributes.  Inside a shoreline the terrain
  lies below the water level or the shore z-fights.
* Soft particles: a bounded density volume or a radial-alpha sprite; bare `PointsMaterial`
  squares, spheres or polygon sparks are `hard_edged_particles`.
* A `blender_glb` hero is built in its own workspace (track `static_object`, language
  `blender`) and its `artifacts/object.glb` copied to `public/assets/<snake>.glb`.

## The loop
write → `3dcode build` → `3dcode check-placement` (every floating / sunken / interpenetrating
row with the metres to move it) → fix → `3dcode build` → `3dcode scene-views` → read the frame
table (`mean_lum`, `lum_std`, `dark_frac`, `blown_frac`, `content_frac`, MEASURED MOTION per
camera) and OPEN the sheet → `3dcode scene-probe` for the census → repeat → `3dcode check`
clean.  A module that throws takes the whole frame black; a zone that throws is dropped and
scores nothing — never wrap it in try/catch to hide it.  A simple scene that is seated, lit and
legible beats an elaborate one that floats in the dark.
