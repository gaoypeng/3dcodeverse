# `src/lib/` — the shipped effect library, an optional toolbox

Harness code copied into the workspace by `3dcode init`, compiled and rendered on THIS
renderer; it carries the depth and fog chunks a hand-written shader silently loses.  Importing
it is allowed where "never import a helper SDK" forbids everything else; edits to it are
reverted.  Import with a relative path (`src/zones/x.js` and `src/assets/x.js`:
`'../lib/grass.js'`; `src/scene.js`: `'./lib/grass.js'`), and only the names you CALL (120
unused imported names were measured over six scenes).  Each module's JSDoc lists its options
and bounds: an option left out takes its default, an invalid one throws a RangeError naming it.

| Want | Call |
| --- | --- |
| a close-view fractured rock or a talus field | `makeRock({ type: 'sandstone', size: [2, 1.5, 2], seed, moisture: 0.2 })` · `makeRockField` — `lib/rock.js`; sandstone / granite / basalt; rests at local y≈0 |
| a grassy surface | `makeGrass({ extent, density, height, heightAt, patchy })` — `lib/grass.js` |
| a complete near/middle-distance tree or shrub | `makeTree({ species: 'oak', height: 6, crownRadius: 2.5, leafDensity: 1, seed, wind: { dir: [1,0.3], strength: 0.5, speed: 1 } })` · `makeShrub` — `lib/tree.js`; oak / birch / willow, separate leaf cards, root at y=0, envelope in `userData.bounds`; heavy by default (species default 10–16 `leafSegments` for close views) — `leafSegments: 4`, lower `maxLeaves` / `leafDensity` for repeats, distant imposters when leaves are unresolved |
| trees, bushes, leaves | `patchLeafSSS` · `patchWind` · `patchRootContact` — `lib/foliage_shade.js` |
| ground and cliff geometry | `ground({ size, rand: mulberry32(seed), relief, flat })` → `{ mesh, height }` (the env's `heightAt` must return its real height; with no seeded `rand` it is flat; a floor, terraces or a canyon are not this call) · `cliff({ length, height, rand })` — `lib/terrain.js` |
| countryside continuing beyond the content | `makeOutskirts({ inner, baseY, heightAt, shellRadius, colors, seed })` — `lib/environment.js`; one option for a rural setting |
| the sun, the fill and the baked environment | `sunRig({ azimuth, elevation, bounds })` — `lib/environment.js` → `{ sun, fill, sunDisc, envTex, sunDir }`; add the lights, `scene.environment = rig.envTex` |
| a rectangular interior's walls, ceiling and openings | `roomShell({ center, extents, openings, thickness, wallColor, ceilingColor, glazed, frameColor, bay })` — `lib/environment.js`; walls sit OUTWARD of the box you pass (its faces are the inner faces; it need not be the plan bounds), openings are cut as span / sill / lintel panels; `glazed: true` for a conservatory / greenhouse |
| the sky itself | `makeSky(scene, { rig })` — `lib/sky.js`; the dome IS the backdrop; keeps a `scene.background` already set and gives an unset one its horizon colour |
| paper, wax, jade, petals; oil film, beetle shell | `patchTranslucency` · `patchIridescence` — `lib/finish.js` |
| a textured standard material without a texture file | `brick` · `granite` · `cobble` · `asphalt` · `weatheredWood` · `brushedSteel` · `fabric` · `foliage` · `soil` · `skin` · `glass` (21 in all) · `tint(mat, variant)` — `lib/materials.js` |
| noise on the CPU | `fbm2` · `fbm3` · `mulberry32` · `displaceY` — `lib/noise.js` |
| your own shader, safely | `makeShaderMaterial` · `patchStandard` · `shadowLike` · `tickShaders` · `clonePatchedMaterial` — `lib/shader.js` |
| a scoped synchronous offscreen capture | `withRendererState(renderer, callback)` — `lib/shader.js`; restores target / viewport / scissor / tone mapping / clear state even after an error (transforms and visibility stay yours) |
| explicit cleanup for a procedural group | `snapshotResources(group)` · `attachDisposal(group, resources)` — `lib/lifecycle.js`; textures are borrowed unless added, disposal is idempotent and ignores later-added props |

Rules that apply to all of them:

1. **`patch*` entries chain** — any number on one material, in any order; prefix your own GLSL
   helpers (the library's are `astra…`).  Copy a patched material with `clonePatchedMaterial`,
   not `.clone()`.
2. **Moving factories carry `userData.update(t, dt)`** (absolute seconds) and
   `userData.dispose()`: store what they return and call `update` exactly ONCE per frame from
   the zone's `userData.update`.  Anything built with `patchStandard` is driven instead by one
   `tickShaders(scene, t)` in `scene.js`'s `update`.  Their dimensions and height queries are
   LOCAL; `userData.sampleHeight(x, z)` returns the surface height or `null` off the patch.
3. **Shadows**: trees cast real moving shadows by default, grass behind `shadows: true`; a
   displaced lattice of your own needs `shadowLike` or the shadow is cast by the undisplaced
   geometry.
4. **This renderer**: a post chain is on (GTAO, selective emissive bloom, identity grade unless
   `scene.userData.grade = { exposure, contrast, saturation, warmth, tint }`); ACES at exposure
   1.0, sRGB out; `logarithmicDepthBuffer` is off (the library's log-depth chunks compile to
   nothing — leave them).  Pass `sunDir` / the rig; never hardcode a light colour in an effect.

A closed displaced surface for a crown (an icosahedron with noise) has no silhouette, lets no
light through and carries one hue — measured hue spread 3.6° vs 9.4° for separate leaf cards
(a photograph: 25–45).  Any construction that gets there is fine; `makeTree` is the shipped one.
