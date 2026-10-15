# Scene (multi-file three.js + GLSL) authoring contract — track `scene`, language `scene_threejs`

## Files (ES modules, three r182; Y-up, +Z front, meters)
```
src/scene.js        export async function createScene({ THREE, renderer, loaders })
                      → { scene, cameras, update(t, dt) }
src/env.js          export function heightAt(x, z); export function buildEnv(ctx) → { update(t, dt) }
                    (+ export const SUN_AZIMUTH_DEG, a plain number — see "The assembler")
src/zones/<snake>.js   export function build(ctx) → THREE.Group   (ctx = { THREE, scene, renderer, loaders, heightAt, env, assets })
src/assets/<snake>.js  export function build<Asset>(THREE, opts = {}) → THREE.Group   (procedural, Y-up, lowest point on y = 0)
src/shaders/<snake>.js export function make<Name>Material(THREE, opts = {}) → THREE.ShaderMaterial | patched material
src/lib/*.js        HARNESS-OWNED effect library — an optional toolbox: import and call; writes to it are reverted
public/assets/<snake>.glb   (optional) Blender-built assets — the assembled scene.js preloads each into ctx.assets['<snake>']; zones clone it (its clips play by themselves)
```
The harness serves the workspace root over http (`public/assets/` is mounted at
`/assets/`), imports `src/scene.js` in headless Chrome with the real `THREE`, a configured
`WebGLRenderer` (shadows on, ACES tone mapping, sRGB output; logarithmic depth may be on
or off — write shaders that work either way) and `loaders = { gltf, texture, cube,
manager }` (GLTFLoader etc.).  It awaits `createScene`, validates the return value, then
renders every camera at t = 0 and t = 1.5 s (calling `update`) and takes probes (tris, draw
calls, console/shader errors, camera-inside-geometry, dark/blown/flat frames, content
coverage, fps, the difference between the two frames).  The plan's **bounds** are the region
the content census and the placement gate measure against; the world a camera sees may
extend as far past them as the brief needs.

## createScene return value
* `scene`: a `THREE.Scene`.  Whatever a camera can see must terminate in something the brief
  justifies — sky, a wall, a ceiling, fog, darkness.  The census reads `scene.fog` and
  `scene.background` on every boot (unset = `no_fog` / `no_background`, ERROR), the world-edge
  finding reads a ground edge meeting the sky, and the flat-frame gate reads a single-band frame.
* `cameras`: a non-empty list of **plain objects** `{ name, position: [x, y, z], lookAt: [x, y, z], fov }`
  (the harness builds the PerspectiveCameras: aspect 16/9, near 0.1, far from bounds).  The
  plan's camera list decides how many; each is rendered twice and gated.  Names PascalCase from
  the plan.  `cameras[0]` is measured as the establishing view:
  ≥ 20 % of its frame must be content, not sky or ground; what it establishes — a street
  receding, a canyon wall, a room — is the brief's, and it need not contain the whole bounds.
  fov and eye height are the brief's; the gate requires the eye ≥ 0.3 m above the ground under
  it (`camera_low` WARN; `camera_high` WARN above 80 m), ≥ 0.5 m from every surface
  (`camera_in_geometry`, ERROR, cap 0.50), no more than 5 of its 9 sight rays ending within
  1.5 m (`camera_blocked`, ERROR, cap 0.50), and the line of sight to `lookAt` not cut in its
  first half.  Compute y from `heightAt(x, z)` (or the floor it stands on): a camera below the
  ground under it is `camera_underground` (cap 0.50).
* `update(t, dt)`: advance animation (t seconds since start, dt seconds).  MUST be cheap
  (no allocations, no traversals, no material or shader creation — cache lists of animated
  objects / materials at build time).  Deterministic: the same t always gives the same frame.

## The assembler (staged runs)
In a staged run `src/scene.js` is **written by the harness** from your `src/env.js` and
`src/zones/*.js`, and overwrites anything you put there.  That generated file is the only
caller of your modules, so these signatures are the whole interface — get one wrong and the
module is silently dropped or its animation never runs:

```js
const ctx = { THREE, scene, renderer, loaders, heightAt, assets };
const env = (await buildEnv(ctx)) || {};          // src/env.js → ONE argument: ctx
ctx.env = env;
const g = await build(ctx);                       // src/zones/<snake>.js → the export is named `build`
if (!g || !g.isObject3D) throw new Error(...);    // must return a Group / Object3D
scene.add(g);
function update(t, dt) {
  if (env.update) env.update(t, dt);              // env animation ONLY via the returned update
  for (const z of zones) if (z.userData.update) z.userData.update(t, dt);   // zone animation ONLY via userData.update
}
```
* `tickEnv`, `userData.tick`, `buildEnv(THREE, scene)` and `build<Zone>(ctx)` are NEVER called.
  An asset's own `userData.update` runs only if its zone caches it at build time and calls it
  from the zone's `userData.update`; a hook that is never called stands still, and calling a
  missing hook throws.
* `src/env.js` also exports `heightAt(x, z)` — the surface every zone seats on: deterministic,
  cheap, `0` for a flat floor; any walkable surface the brief has (a path, a road, a deck) is part
  of it, and the placement gate measures against it, so it must agree with the mesh — and
  `SUN_AZIMUTH_DEG` (a plain numeric `export const`, read by regex to put the overview cameras on
  the sun side; omitted, the rig guesses 45°).  Nothing else in `env.js` is read by the harness:
  whatever else it exports (shared materials, a water level, a route) is a convention between
  you and the zones, named as you like, and zones may `import` it.
* Without the assembler the harness asks for `src/scene.js` by hand: exactly the wiring above,
  returning `{ scene, cameras, update }`.

## Imports (only these resolve)
`three`, `three/addons/*` (GLTFLoader, BufferGeometryUtils, Sky, Water, EffectComposer…).
Relative imports between your own files, and `src/lib/*.js`.  No CDN, no other packages,
no `three/webgpu`, no `three/tsl`.

### Separate shader source files

Write `.vert`, `.frag` and `.glsl` files under `src/shaders/`. The local server serves
them as text. In async `createScene`, use the supplied loading manager and resolve
paths relative to the JavaScript module, so the scene also works when packaged:

```js
const text = new THREE.FileLoader(loaders.manager); // Default response is text.
const [vertexShader, fragmentShader] = await Promise.all([
  text.loadAsync(new URL('./shaders/object.vert', import.meta.url).href),
  text.loadAsync(new URL('./shaders/object.frag', import.meta.url).href),
]);
const material = new THREE.ShaderMaterial({ vertexShader, fragmentShader });
```

This example path is for `src/scene.js`; from a module in `src/scenes/`, use
`../shaders/`. An optional `.glsl` helper file may be loaded and explicitly joined
to the shader text. There is no general file `#include` resolver: Three's
`#include <chunk>` references only its built-in shader chunks. There is no bundler,
so `import shader from './file.frag'` and `?raw` do not load shader text.

Await loading before returning the scene; missing files must reject normally rather
than produce an empty shader. Loading is local to the workspace, never a CDN or
external URL. Reusable asset builders remain synchronous and Node-compatible;
perform browser loading in `createScene`, and pass already loaded text where needed.
`update(t, dt)` changes uniforms or transforms, without loading or shader compilation.
For GLSL 3 use `glslVersion: THREE.GLSL3`, explicit `in`/`out` varyings and a fragment
output; let Three add the version line instead of adding a second `#version`.
A hand-written shader loses Three's fog and depth chunks unless it is built with
`makeShaderMaterial` / `patchStandard` from `lib/shader.js`, which keep them.

`src/lib/` is NOT a helper SDK and the "raw language only" law does not exclude it: it is
harness-owned source shipped into this workspace, every module compiled and rendered on
this renderer, and writes to it are reverted.  It is an optional toolbox — the want → call
table ships with this brief; use an entry when it fits the brief, write your own when it does
not.  Import only the names you call (measured over six scenes: 120 imported names were never
used — dead weight every later round re-reads), and only names the catalog prints for a module:
importing a name a module does not export is a SyntaxError that drops the whole zone.

The harness renders scene pictures THROUGH a post chain (GTAO + a selective emissive
bloom + a grade that is identity unless `scene.userData.grade` is set), and the scene must
still read without it.  Emissives DO bloom: author them at the practical level (`PRACTICAL`
in `lib/environment.js`; peak 1.5–4, not 20); a material opts in or out with
`material.userData.bloom = false | true | <colour>`.  Do not build an `EffectComposer`:
nothing reads one.  `--no-post` / `C3D_POST=0` turns the chain off.

## Forbidden
`document.*` / `window.*` except `window.innerWidth` and `document.createElement('canvas')` for
a procedural texture (the one DOM call the lint allows; no other DOM); external network requests or arbitrary `fetch`/XHR (use `loaders.gltf` for assets
and the local FileLoader pattern above for shader text); `Math.random()` (use `mulberry32(seed)` from `lib/noise.js`);
`Date.now()`/`performance.now()` (use `t`); `requestAnimationFrame`; creating a renderer;
changing renderer settings; environment sniffing (`navigator.userAgent`, headless checks).
Textures: the shipped procedural materials (including their DataTextures) or the provided
`loaders.texture` for files actually present in the workspace — nothing fetched.

## What is measured (the gates and probes, with their thresholds)
* **Placement** (`check_placement`; the `scene_placement` gate runs on every build).  Each direct
  child of a zone group is one placed object; from its lowest vertices the probe looks down for
  the nearest surface and up for one passing through it.  A gap under the foot > 0.05 m outdoors
  / 0.02 m indoors = `floating` (ERROR at 0.08 m, or more than two floaters); buried > 0.10 m =
  `sunken` (ERROR at 0.20 m and > 20 % of the height; things that are buried, slope-following
  or driven in by nature are exempt, as is a foot at or under water); a bbox touching nothing =
  `unsupported`; two boxes sharing > 10 % of the smaller one = `interpenetration` (ERROR at
  35 %).  A Group holding several props is measured as ONE object whose box is their union, so
  every prop inside it reads as interpenetrating it: one placed thing = one direct child; a prop
  built from parts is one Group; many of one thing is one `InstancedMesh` (instances are not
  checked one by one, but a scattered instance inside a placed thing is that thing's
  interpenetration).  A thing meant to hang in the air carries `obj.userData.placement = 'free'`
  (`free_exempt`: WARN when more than 25 % of the placed objects are tagged, ERROR past 50 %).
  A thing resting on one corner with the others in the air — four or more foot columns, at most
  one touching, the worst gap > 0.05 m — is `cantilevered` (WARN).  A placed asset is measured
  as ONE object against the plan's `approx_size_m`: WARN beyond 2.5 × (or under 1 / 2.5), ERROR
  beyond 4 × (`scale`; the judge's `wrong_scale`, cap 0.75, is perceptual); a part that must
  reach further is a separate object.
* **Bounds and density.**  A PLANNED content placed entirely outside the plan bounds plus
  max(5 m, 10 % of the span) is `out_of_bounds` (a WARN; scenery that continues the place past
  the bounds is never reported); a zone group missing or empty is `zone_empty`; a planned asset
  not placed is `missing_content`; a zone under half the instances its layout counted gets an
  INFO `density_note` (the counts the brief states are its acceptance items).
* **The world's extent** (exteriors only).  Standing geometry ≥ 2 m tall must reach at least
  1.25 × the bounds half-extent from the bounds centre (`world_edge`, a WARN); fog far must be ≥ 2 × the
  plan span — or, for `FogExp2`, 1 / density ≥ span / 1.5 — (`fog_short`: past fog far the land
  turns the background colour while the unfogged sky stays sharp); ≥ 8 same-size copies evenly on a circle round the world's edge is a
  `stamped_ring` (the judge reads it as "identical pyramids stamped in a ring").  What continues
  the world — a street's next block, more forest, open sea, a wall, a skyline on one side, haze —
  is the brief's.
* **Frames** (`scene_frames`, per authored camera): the exposure band (`dark_frame`,
  `blown_frame`, `flat_frame`), camera placement (`camera_in_geometry`, `camera_blocked`,
  `camera_underground`, `camera_low`, `camera_high`) and content fraction (`content_small`) the
  system prompt states; `scene_views(times=[0, 1.5])` prints the frame table and the measured
  motion per camera.  Non-solid cards (haze quads, shafts) near a camera read as geometry to the
  raycast: give them `mesh.raycast = () => {};` — never real geometry.
* **Motion** (`frame_motion`, per authored camera): the t = 0 and t = 1.5 s frames are
  differenced; a camera MOVES when ≥ 0.4 % of its pixels change by more than 8/255 (or ≥ 0.08 %
  by more than 60/255).  No authored camera moving is `no_motion` (ERROR); the judge's
  `nothing_moves` follows it.
* **A hero GLB in frame** (`hero_unseen`): a `public/assets/*.glb` hero covering < 0.5 % of the
  frame in EVERY authored camera is an ERROR; < 2 % in the camera the plan names for it is a WARN.
* **Shaders and errors**: a console or shader error in any module is a build finding;
  `shader_probe` compiles a material before it renders black.
* **Assets**: a procedural asset module is imported and built in node when written; one instance
  is budgeted at 15 000 triangles, its lowest point at y = 0, its footprint centred on the origin,
  its size the plan's (the check errors beyond 2.5 × or under 0.4 × the plan's on any axis, and on
  a single-box blockout).

## Naming
Zones are Groups named PascalCase (plan zone names); assets Groups PascalCase; every
animated object has a stable `.name`; ShaderMaterials `mat.name = 'Water'`; the ground mesh is
named `Ground`; backdrop geometry beyond the content is named `Sky…` (classified as sky, so it
does not inflate the content box or the overview fit); an interior's lid meshes are named
`Ceiling…` / `RoomShell…` so the overview rig can lift them.

## Language facts that are build errors or silent failures
* `Math.random()` gives different geometry every build and un-reproducible renders: `mulberry32(seed)`.
* A sky-dome material needs `fog: false`, `side: THREE.BackSide`, `depthWrite: false` (and the
  tonemap / colorspace chunks `makeShaderMaterial` adds) or it renders black or fogged.
* `mergeGeometries` returns `null` on mismatched attributes — check before `computeVertexNormals`.
* `walk(fig, t)` (lib/figure.js) runs only inside `userData.update(t)`; at build time it throws
  `time must be finite`.
* A GLB hero is visible only if the zone clones `ctx.assets['<snake>']` (nothing loads a GLB
  itself); its pivot is at its base; `clone.userData.clipOffset = <seconds>` de-phases a copy.
* `makeText` (lib/signage.js) is async: await it ONCE at module top level, `.clone()` per copy.
* Water: inside any shoreline the terrain must lie below the water level or the shore z-fights.
* A camera below the terrain under it, or inside a mesh, is a gate ERROR capped at 0.50: the
  plan's camera heights are absolute metres, so compute y from `heightAt(x, z)` (or the floor
  it stands on), never from a guess.
* Shadows: `castShadow` on the light AND the meshes, `receiveShadow` on what they fall on.  A
  shadow texel larger than ~0.05 m over the content reads as jagged (judged); `sunRig({ bounds })`
  fits frustum, map size and bias to the radius it is given, and a hand-set `sun.shadow.*`
  must meet the same texel size.
* `update` that allocates (`new Vector3`, `getSize` per frame) or traverses hitches the GC:
  hoist scratch objects to module scope and cache lists at build time.

## The env module's shape (the interface, nothing else; the assembler block above is the rest)
`src/env.js`
```js
import * as THREE from 'three';
export const SUN_AZIMUTH_DEG = 250;        // the plan's light, as a plain number (0 = +X, 90 = +Z; read by regex)
export function heightAt(x, z) { return 0; }   // the brief's surface — any function of (x, z) that agrees with the ground mesh
export function buildEnv({ THREE, scene }) {
  // the brief's sky, light, ground (a mesh named 'Ground…' sampled from heightAt) and atmosphere go here:
  // scene.fog + scene.background set (no_fog / no_background), scene.environment an env texture
  return { update(t, dt) {} };               // the ONLY env hook the assembler calls
}
```
A zone module is `export function build(ctx) → THREE.Group` with its animation on
`group.userData.update`; a hand-written `src/scene.js` is the assembler block above verbatim,
returning `{ scene, cameras, update }` with the plan's camera list.
