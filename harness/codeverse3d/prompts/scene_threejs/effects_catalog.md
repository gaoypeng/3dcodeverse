# The shipped effect library — an optional toolbox

`src/lib/` is in your workspace already: the shipped modules are compiled
and rendered on THIS renderer.  They carry the depth and fog chunks a
hand-written shader silently loses, and they are not an outside package — they
are harness code shipped into the workspace, so importing them is allowed where
"never import a helper SDK" still forbids everything else.

Each entry lists what it does and its API.  Use one when it fits the brief, or write
your own; `makeShaderMaterial` / `patchStandard` from `lib/shader.js` keep Three's fog
and depth chunks in a shader of your own if you want them.

Import with a relative path from the file you are writing — `src/zones/x.js`
and `src/assets/x.js` say `import { makeGrass } from '../lib/grass.js';`,
`src/scene.js` says `'./lib/grass.js'`.

| Want | Call |
| --- | --- |
| a close-view fractured rock or a talus field | `makeRock({ type: 'sandstone', size: [2, 1.5, 2], seed, moisture: 0.2 })` · `makeRockField` — `lib/rock.js`; types sandstone / granite / basalt; closed geometry, stratification and weathering. Rock rests at local y≈0; place using terrain height. |
| a grassy surface | `makeGrass({ extent, density, height, heightAt, patchy })` — `lib/grass.js` |
| a complete near/middle-distance tree or shrub | `makeTree({ species: 'oak', height: 6, crownRadius: 2.5, leafDensity: 1, seed, wind: { dir: [1,0.3], strength: 0.5, speed: 1 } })` · `makeShrub` — `lib/tree.js`; oak/birch/willow, connected branches, individual leaves and matching moving shadows. Root at y=0, actual envelope in `userData.bounds`. A default tree is heavy geometry. `leafSegments: 4` reduces per-leaf geometry while preserving crown density; species defaults are 10–16 for close views. Also lower `opts.maxLeaves`/`opts.leafDensity`, or use distant imposters when leaves are unresolved. |
| trees, bushes, leaves | `patchLeafSSS` · `patchWind` · `patchRootContact` — `lib/foliage_shade.js` |
| ground and cliff geometry | `ground({ size, rand: mulberry32(seed), relief, flat })` returns `{ mesh, height }` — one terrain option (a displaced plane with a seeded heightfield and an optional flat mask; with no seeded rand it is flat); whatever the env's heightAt(x, z) returns must be its real height · `cliff({ length, height, rand })` — `lib/terrain.js`.  The brief decides the landform; a floor, terraces or a canyon are not this call. |
| countryside continuing beyond the content | `makeOutskirts({ inner, baseY, heightAt, shellRadius, colors, seed })` — `lib/environment.js`; seam-matched relief, field patchwork and wooded clusters — one option for a rural setting.  The requirement is that no camera sees the ground end against the sky (`world_edge_visible`) or a diorama on an empty plane (judged); how the world continues — a street that keeps going, a sea, a canyon wall, a city, fog that closes — is the brief's. |
| a Blender hero's own keyframed motion (the GLB carries a clip) | nothing to call — the assembled scene plays every clone's clips; `clone.userData.clipOffset = 0.7` de-phases a copy |
| the sun, the fill and the baked environment | `sunRig({ azimuth, elevation })` — `lib/environment.js`; returns `{ sun, fill, sunDisc, envTex, sunDir }`.  Add the lights, assign `scene.environment = rig.envTex` |
| a rectangular interior's walls, ceiling and window/door openings | `roomShell({ center, extents, openings, thickness, wallColor, ceilingColor, glazed, frameColor, bay })` — `lib/environment.js`; walls sit outward of the box you pass (inner face = that box's face, which need not be the plan bounds), openings cut as span / sill / lintel panels; glazed: true makes every wall and the roof glass on an iron frame grid (a conservatory, greenhouse, glass atrium).  One option for a box-shaped room; the requirement for any interior is that it reads as enclosed from every camera (six runs measured "not enclosed, a diorama on a flat plane" with no walls or roof) |
| the sky itself | `makeSky(scene, { rig })` — `lib/sky.js`; the dome IS the backdrop; it keeps a `scene.background` already set and gives an unset one its horizon colour |
| paper, wax, jade, petals; oil film, beetle shell | `patchTranslucency` · `patchIridescence` — `lib/finish.js` |
| a textured standard material without a texture file | `brick` · `granite` · `cobble` · `asphalt` · `weatheredWood` · `brushedSteel` · `fabric` · `foliage` · `soil` · `skin` · `glass` (21 in all) · `tint(mat, variant)` — `lib/materials.js` |
| noise on the CPU (heightfields the shader must agree with) | `fbm2` · `fbm3` · `mulberry32` · `displaceY` — `lib/noise.js` |
| your own shader, safely | `makeShaderMaterial` · `patchStandard` · `shadowLike` · `tickShaders` — `lib/shader.js` |
| a scoped synchronous offscreen capture | `withRendererState(renderer, callback)` — `lib/shader.js`; restores target/cube/mip, viewport/scissor, XR, shadow auto-update, tone mapping and clear state even after an error. Object transforms/visibility and other modified flags remain the caller's responsibility. No recursive-reflector coordinator is implied. |
| explicit cleanup for a procedural group you build | `snapshotResources(group)` · `attachDisposal(group, resources)` — `lib/lifecycle.js`; capture construction-owned geometry/materials before attaching borrowed children. Textures are borrowed unless explicitly added to the resource set. Disposal is idempotent and does not traverse later-added props. |

## The four rules that apply to all of them

**The natural-element factories use one lifecycle.** Tree, shrub, rock and grass provide
`object.userData.update(t, dt)` (absolute seconds)
and `object.userData.dispose()`. Store the returned objects and update each ONCE
from the zone's `userData.update`. Their dimensions, paths and height queries are
LOCAL coordinates; move/rotate the whole returned object to place it. Call the
factory before positioning props that sample its surface: `userData.sampleHeight(x, z)`
returns the surface height there, or `null` off the patch. It
never throws, so test for `null` before seating a prop. These are realtime
procedural effects, not fluid/combustion solvers. Read the module's JSDoc for
options and bounds: an option left out or `null` takes its default, and an invalid
one throws a RangeError that names it.
The direction toward the sun is `sunDir` in every factory.

**1. `patch*` entries CHAIN.**  Apply as many as a surface deserves to one
material, in any order — they are composed into one program.  Two patches may
not define the same GLSL helper name with different bodies; the library prefixes
its own (`astra…`), so prefix yours.

Use `clonePatchedMaterial(material)` from `lib/shader.js` to copy a material
that already carries patches. Three's ordinary `.clone()` drops the shader hook
and JSON-copies typed uniforms. The library helper replays its registered chain,
copies vector/colour/matrix uniforms and borrows texture references. Custom
external `onBeforeCompile` wrappers must still be reapplied by their owner.

**2. Moving factories carry a per-frame hook: `userData.update(t, dt)`.** Call it
exactly once per frame.
The host drives the scene's `update(t, dt)`. Fan out in `scene.js`:

```js
function update(t, dt) {
  if (env.update) env.update(t, dt);
  for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
}
```

and in a zone, call the effect's own hook from the group's `update`:

```js
const lawn = makeGrass({ extent: 6, heightAt });
g.add(lawn);
g.userData.update = (t, dt) => { lawn.userData.update(t, dt); };
```

Calling a missing hook throws and can stop the whole scene's animation.
Anything built with `patchStandard` is driven instead by one
`tickShaders(scene, t)` — call it once, in `scene.js`'s `update`.  An effect
whose hook is never called stands still.

**3. Shadows.**  Displaced vegetation casts REAL moving shadows: trees by
default; grass behind `shadows: true` (tens of thousands of instances in the
shadow map is a cost you opt into).  A
displaced lattice of your own gets the same through `shadowLike` in
`lib/shader.js` — without it the shadow is cast by the UNDISPLACED geometry.

**4. Import only what you CALL.**  Measured over the reference battery's first
six scenes: 120 imported names were never used.  A line that lists a module's
exports gets pasted whole and the unused half becomes dead weight every later
round has to read.

## What differs on this renderer

* **There IS a post chain** (`runtime_js/lib/browser/post.js`, on by default for
  scene renders): GTAO, a SELECTIVE emissive bloom, and a grade that is identity
  until the scene asks.  So emissives bloom — author them at peak **1.5–4**, not
  20, and let the bloom do the rest.  A material can opt in or out explicitly
  with `material.userData.bloom = false | true | <colour>`.
* Grading is per scene: set `scene.userData.grade = { exposure, contrast,
  saturation, warmth, tint, … }`.  Leave it unset for identity.
* Tone mapping is ACES at exposure **1.0** and output is sRGB.  Non-emissive
  albedos belong in **0.02–0.8** linear: a pure black albedo renders as void and a
  pure white one blows out (the `dark_frame` / `blown` gates read both).
* `logarithmicDepthBuffer` is OFF in this harness, so the library's log-depth chunks
  compile to nothing here.  They cost nothing; leave them alone.
* Everything reads `scene.environment` and the sun direction.  Do not hardcode a
  light colour inside an effect; pass `sunDir` / the rig.

## What a crown that reads as leaves measures

A closed displaced surface (an icosahedron with noise, however many octaves) has no
silhouette to read, lets no light through, and carries ONE hue: a measured avenue
stacked 32 of them per tree and shipped what the renderer flagged as "a single flat
untextured material, an unreadable slab" (judged `asset_quality_detail`).  Measured on
the same tree: hue spread across the crown went from 3.6° to 9.4° and the 5–95 range
from 10 to 22 when the crown became separate leaf cards — a photograph carries 25–45.
Any construction that gets there is fine; `makeTree` / `makeShrub` (separate leaf cards)
is the shipped option, and a stylised brief (low-poly, paper-craft) may legitimately want
something else.
