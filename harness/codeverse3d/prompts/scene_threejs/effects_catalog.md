# The shipped effect library — call these before writing your own

`src/lib/` is in your workspace already: the shipped modules are compiled
and rendered on THIS renderer.  They carry the depth and fog chunks a
hand-written shader silently loses, and they are not an outside package — they
are harness code shipped into the workspace, so importing them is allowed where
"never import a helper SDK" still forbids everything else.

Reach for one FIRST.  Write your own only for something no entry here covers,
and then build it with `makeShaderMaterial` / `patchStandard` from
`lib/shader.js`.

Import with a relative path from the file you are writing — `src/zones/x.js`
says `import { makeGrass } from '../lib/grass.js';`, `src/scene.js` says
`'./lib/grass.js'`.

| Want | Call |
| --- | --- |
| a small flame or wax candle | `makeFire({ radius, height, seed, quality: 'high' })` · `makeCandle({ radius, height, seed, lit: true })` — `lib/fire.js`; volumetric flame, moving illumination, wick and wax. Returns a Group with `userData.update(t, dt)`; use metres (candle radius about 0.025, height 0.15). Fire is transparent: avoid intersecting glass volumes. |
| a campfire, large fuel bed or flames through a building | `makeFireField({ emitters: [{ position: [0,0,0], radius: 0.3, height: 1, strength: 1 }], wind: [0.15,0], smoke: {height: 3, density: 0.65}, occluders: [fuel, walls], quality: 'balanced', seed })` — `lib/firefield.js`; all sources share one flame/soot integration. Wind is local metres/second. Optional borrowed opaque occluders truncate rays, including instancing/alpha cuts; custom deformation needs customDepthMaterial. No glass interleaving, skinned occluders or log-depth capture. Layout/wind are baked: rebuild to change them. Group provides update/dispose, bounds, sampleField(x,y,z,t), sampleEmber(index,t) and cost. Start with one field: each field adds a ray march over 3D textures, plus a depth pass when occluders are given. |
| rising smoke, steam from a cup or vent | `makeSmoke({ height: 3, radius: 0.14, wind: [0.06, 0], riseSpeed: 0.7, seed, quality: 'balanced' })` · `makeSteam({ height: 0.5, radius: 0.03 })` — `lib/smoke.js`; bounded 3D density with upward advection, widening, dilution and internal light attenuation. Mesh origin is the emitter at local y=0. `userData.update(t)`, `userData.sampleDensity(x,y,z,t)` and `userData.dispose()`. Directional/point/ambient scene lights illuminate it. Keep opaque geometry outside its `userData.bounds`; intersections and overlapping transparent volumes are not depth-resolved. |
| the OCEAN, a lake or any sizeable water body | `makeOceanSurface({ width, depth, waveHeight, wavelength, windDirection: [1, 0.2], seed })` — `lib/ocean.js`, the ocean: dispersive Gerstner waves on real displaced geometry, reflected scene, crest foam; Group with `userData.update(t, dt)` and `userData.sampleHeight(x, z, t)`. Its flat option, for calm or distant water where no wave shape reads (a pond, a far lake): `makeOcean(w, d, { sunDir })` — `lib/water.js`, a normal-mapped mirror plane. ONE reflective water body per scene. |
| a curved shallow STREAM | `makeStream({ points: [[0, 0.6, -8], [2, 0.3, 0], [0, 0, 8]], width: 2, depth: 0.3, speed: 1, seed })` — `lib/stream.js`; points run upstream → downstream, decreasing Y. Refracts the opaque bed/scene through depth-varying water; flowing ripples, stone wakes and approximate caustics. `waterColor: 0x80b6a6, attenuationDistance: 3` controls volume absorption over a distance in metres. Optional `reflectionSize: 512` captures the banks; default 0, and at most ONE planar reflector per scene. Group with `userData.update(t, dt)`, `userData.sample(u, lateral, t)`. Keep terrain BELOW the bed inside the channel. |
| a falling sheet of water over a weir | `makeWaterfall({ width: 2, height: 3, speed: 1, thickness: 0.045, foam: 0.65, seed })` — `lib/waterfall.js`; gravity accelerates and thins the sheet, with advected ripples, local aeration and impact spray. Origin is impact water level, lip is `[0,height,0]`, flow is +Z. Supply the upstream water, cliff and pool. `userData.sample(u,lateral,t)` uses u=0..1 and lateral=-0.5..0.5; `userData.impact` gives the receiving point. No collision/fluid solver; opaque-scene refraction only. |
| thick broken ice with actual open fissures | `makeFracturedIce({ size: [8,6], thickness: 0.28, crackDensity: 0.65, frost: 0.35, seed })` — `lib/ice.js`; closed floes, bevels, internal air and local optical thickness. Nominal top is y=0. `userData.sampleHeight(x,z)` returns local top height or null in a gap. Supply an opaque lakebed/water backing: transparent volumes are not recursively refracted. |
| dunes, wind ripples, sand grains | `makeSandTerrain({ size: [60, 60], duneHeight: 3, duneSpacing: 18, windDirection: [1, 0.3], seed })` — `lib/sand.js`; Group with `userData.sampleHeight(x, z)` and `userData.update(t, dt)`. Sample height for every prop. |
| a close-view fractured rock or a talus field | `makeRock({ type: 'sandstone', size: [2, 1.5, 2], seed, moisture: 0.2 })` · `makeRockField` — `lib/rock.js`; types sandstone / granite / basalt; closed geometry, stratification and weathering. Rock rests at local y≈0; place using terrain height. |
| close-view grass with folded blades and flowing wind | `makeMeadow({ size: [8, 8], height: 0.3, density: 900, heightAt, mask, seed, wind: { dir: [1, 0.3], strength: 1.2, speed: 1 } })` — `lib/meadow.js`; its wind is read by grass.js's `windOf`, so one wind object moves meadow, grass and trees alike; mask(x,z) is 0..1 coverage; Group with `userData.update(t, dt)`. Set `ground: false` over an existing terrain. Fit the sun shadow frustum tightly; blades need millimetre-scale shadow texels. |
| a grassy surface | `makeGrass({ extent, density, height, heightAt, patchy })` — `lib/grass.js` |
| a complete near/middle-distance tree or shrub | `makeTree({ species: 'oak', height: 6, crownRadius: 2.5, leafDensity: 1, seed, wind: { dir: [1,0.3], strength: 0.5, speed: 1 } })` · `makeShrub` — `lib/tree.js`; oak/birch/willow, connected branches, individual leaves and matching moving shadows. Root at y=0, actual envelope in `userData.bounds`. A default tree is heavy geometry. `leafSegments: 4` reduces per-leaf geometry while preserving crown density; species defaults are 10–16 for close views. Also lower `opts.maxLeaves`/`opts.leafDensity`, or use distant imposters when leaves are unresolved. |
| A CROWN OF LEAVES — every broadleaf tree, hedge and bush | `makeCanopy({ crowns: [{position, radius, height}] — or [{position, size:[x,y,z]}] for a mass that is not a ball, which is how a HEDGE is built — color, hue, wind })` — `lib/canopy.js`.  NEVER a displaced sphere: see the rule at the end. |
| trees, bushes, leaves | `patchLeafSSS` · `patchWind` · `patchRootContact` — `lib/foliage_shade.js` |
| bark, and a forest at distance | `patchBark` · `makeImposters` — `lib/woodland.js` |
| light through a canopy | `patchDappledLight` · `patchCanopyShade` — `lib/dapple.js`; goes on the surface being LIT, not on the tree |
| flowers, drifting petals or leaves | `makeFlowers` · `makeFalling` — `lib/flowers.js` |
| midges, butterflies, fireflies; reeds in water | `makeInsects` · `makeReeds` — `lib/smalllife.js` |
| birds or fish in motion | `makeFlock({ count, kind, extent, height, path })` — `lib/flock.js` |
| mist over moving water, spray where it lands | `makeWaterMist` · `makeSpray` — `lib/watermist.js` |
| a waterline | `patchShoreWet` · `patchShoreFoam` · `patchShallowWater` — `lib/waterside.js` |
| anything BELOW the water, rain rings, thin ice | `patchUnderwater` · `makeRainRings` · `patchThinIce` — `lib/submerged.js` |
| the moving net of light under water | `patchCaustics(material, { level, sunDir })` — `lib/caustics.js`; goes on the surface being LIT, not on the water |
| moss, damp near water, dried mud | `patchMoss` · `patchMoisture` · `patchCrackedMud` — `lib/damp.js` |
| a polished or wet floor | `makeMirrorFloor(w, d)` — `lib/wetground.js`; re-renders the scene like the water bodies — use one or the other |
| rock, cliff, ground | `patchTriplanar` · `patchSlopeSplat` — `lib/terrain_shade.js` |
| ground and cliff geometry | `ground({ size, rand: mulberry32(seed), relief, flat })` returns `{ mesh, height }` — seat every asset at height(x, z); with no seeded rand the ground is flat · `cliff({ length, height, rand })` — `lib/terrain.js` |
| placing a camera or a prop by intent | `seat` · `establishingShot` · `faceToward` · `alongPath` · `crowdOn` — `lib/place.js` |
| a cliff that reads as rock | `patchRockStrata` · `patchErosionStreaks` — `lib/strata.js`; bedding by world altitude, so every cliff in a scene shares one bedding plane |
| the land between the content and the horizon | `makeOutskirts({ inner, baseY, heightAt, shellRadius, colors, seed })` — `lib/environment.js`; seam-matched relief, field patchwork and wooded clusters.  A bare oversized ground plane measured as "a diorama on a vast, empty flat plane" |
| a Blender hero's own keyframed motion (the GLB carries a clip) | nothing to call — the assembled scene plays every clone's clips; `clone.userData.clipOffset = 0.7` de-phases a copy |
| the sun, the fill and the baked environment | `sunRig({ azimuth, elevation })` — `lib/environment.js`; returns `{ sun, fill, sunDisc, envTex, sunDir }`.  Add the lights, assign `scene.environment = rig.envTex` |
| an interior's walls, ceiling and window/door openings | `roomShell({ center, extents, openings, thickness, wallColor, ceilingColor })` — `lib/environment.js`; walls outward of the bounds' faces (inner face = bounds face), openings cut as span / sill / lintel panels.  The skeleton's env.js builds it from its INTERIOR constant for an interior plan; six runs measured "not enclosed, a diorama on a flat plane" without one |
| the sky itself | `makeSky(scene, { rig })` — `lib/sky.js`; the dome IS the backdrop, so `scene.background` stays null |
| cloud, cirrus | `makeClouds({ preset, sunDir })` · `makeCirrus()` — `lib/clouds.js` |
| volumetric cumulus with internal sunlight attenuation | `makeCloudVolume({ size: [600,180,360], coverage: 0.55, density: 0.028, wind: [2.5,0.4], seed, quality: 'balanced' })` — `lib/cloudvolume.js`; Mesh centred on its local origin. Move it to cloud altitude. Wind advects density/erosion inside a bounded billow scaffold; translate the Mesh to move the whole cloud. `userData.sampleDensity(x,y,z,t)` mirrors the GPU field. Use one bounded sky volume and inspect its screen cost. No terrain shadows or opaque-depth intersections. |
| stars, the Milky Way, aurora, heat shimmer | `makeStars` · `makeAurora` · `makeHeatShimmer` — `lib/celestial.js` |
| fog on the ground, distance haze | `makeHeightFog` · `patchAerialPerspective` — `lib/atmosphere.js` |
| shafts of light | `makeGodRays({ count, height, sunDir, ambient })` — `lib/godrays.js`; the default is daylight — pass `ambient` BELOW 0.35 only for a genuinely unlit interior, where the shafts switch to adding light |
| rain sheets at distance, snowfall, motes | `makeRainVeil` · `makeSnowfall` · `makeMotes` — `lib/veils.js`; weather you see ACROSS the valley, not the drops by the lens |
| rain, splashes, puddles | `makeRain` · `makeSplashes` · `wetten` · `makePuddle` — `lib/rain.js` |
| snow or drifted sand lying on things | `patchSnow` · `patchSand` — `lib/accumulation.js`; one deposit rule across the whole scene is what makes weather read |
| a building, a cottage, a city block | `block` · `cottage` · `cityFabric` — `lib/building.js` |
| lit windows at night | `makeNightWindows(mesh)` · `patchWindowInteriors` — `lib/windows.js` |
| a glazed facade, power lines, a hoarding | `patchCurtainWall` · `makePowerLines` · `makeBillboard` — `lib/urban.js`; the facade spends no second render target |
| a neon sign, and the light it throws | `makeNeonTube` · `patchNeonSpill` · `makeLightTrails` — `lib/neon.js`; a sign that does not light its own wall is a decal |
| readable lettering on a sign | `makeText` (async — await ONCE at module top level, `.clone()` per copy) — `lib/signage.js` |
| a road, a path, a worn surface | `patchRoadSurface` · `patchSeamBand` · `patchTracks` — `lib/roadway.js` |
| foreground cobbles or rectangular stone setts with real joints | `makePaving({ size: [4,4], stoneSize: 0.24, pattern: 'cobble', joint: 0.012, thickness: 0.10, relief: 0.014, seed })` — `lib/paving.js`; merged closed bevelled stones and a recessed joint bed, two meshes and a bounded stone count. Nominal top is y=0, bottom=-thickness. `userData.sampleHeight(x,z)` returns the exact local stone/joint height or null outside; seat props using it. Use material-only cobbles for distant surfaces. |
| a flag, a hanging banner, a wheat field | `makeFlag` · `makeBanner` · `makeWheatField` — `lib/cloth.js`; the wave TRAVELS |
| a surface that has stood somewhere | `patchDripStains` · `patchRust` · `patchDust` — `lib/aging.js` |
| any surface at all | `patchMicroBreakup` · `patchEdgeWear` — `lib/surface_wear.js` |
| paper, wax, jade, petals; oil film, beetle shell | `patchTranslucency` · `patchIridescence` — `lib/finish.js` |
| a person | `figure({ height, fem, rand })` · `sit` · `walk` · `carry` — `lib/figure.js`; a pose is one of those three calls on the figure, not an option — left alone it stands |
| the same asset hundreds of times, in one draw call | `instanceAsset` · `scatterGrid` — `lib/instancing.js` · `mergeStatic(meshes)` — `lib/merge.js` |
| a textured standard material without a texture file | `brick` · `granite` · `cobble` · `asphalt` · `weatheredWood` · `brushedSteel` · `fabric` · `foliage` · `soil` · `skin` · `glass` (21 in all) · `tint(mat, variant)` — `lib/materials.js` |
| noise on the CPU (heightfields the shader must agree with) | `fbm2` · `fbm3` · `mulberry32` · `displaceY` — `lib/noise.js` |
| your own shader, safely | `makeShaderMaterial` · `patchStandard` · `shadowLike` · `tickShaders` — `lib/shader.js` |
| a scoped synchronous offscreen capture | `withRendererState(renderer, callback)` — `lib/shader.js`; restores target/cube/mip, viewport/scissor, XR, shadow auto-update, tone mapping and clear state even after an error. Object transforms/visibility and other modified flags remain the caller's responsibility. No recursive-reflector coordinator is implied. |
| explicit cleanup for a procedural group you build | `snapshotResources(group)` · `attachDisposal(group, resources)` — `lib/lifecycle.js`; capture construction-owned geometry/materials before attaching borrowed children. Textures are borrowed unless explicitly added to the resource set. Disposal is idempotent and does not traverse later-added props. |

## The four rules that apply to all of them

**New natural-element factories use one lifecycle.** Fire, fire field, candle, ocean, stream,
sand, meadow, smoke, steam, waterfall, tree, shrub, cloud volume, ice and paving provide
`object.userData.update(t, dt)` (absolute seconds)
and `object.userData.dispose()`. Store the returned objects and update each ONCE
from the zone's `userData.update`. Their dimensions, paths and height queries are
LOCAL coordinates; move/rotate the whole returned object to place it. Call the
factory before positioning props that sample its surface. These are realtime
procedural effects, not fluid/combustion solvers. Read the module's JSDoc for
options and bounds. For older effects, follow the hooks listed below.

**1. `patch*` entries CHAIN.**  Apply as many as a surface deserves to one
material, in any order — they are composed into one program.  Two patches may
not define the same GLSL helper name with different bodies; the library prefixes
its own (`astra…`), so prefix yours.

Use `clonePatchedMaterial(material)` from `lib/shader.js` to copy a material
that already carries patches. Three's ordinary `.clone()` drops the shader hook
and JSON-copies typed uniforms. The library helper replays its registered chain,
copies vector/colour/matrix uniforms and borrows texture references. Custom
external `onBeforeCompile` wrappers must still be reapplied by their owner.

**2. Moving factories carry a per-frame hook.** Prefer `userData.update(t, dt)`;
some older factories use `userData.tick(t, dt)`. Read the factory JSDoc or select
`effect.userData.update ?? effect.userData.tick`, then call that hook exactly once.
The host drives the scene's `update(t, dt)`. Fan out in `scene.js`:

```js
function update(t, dt) {
  if (env.update) env.update(t, dt);
  for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
}
```

and in a zone, call the effect's own hook from the group's `update`:

```js
const rain = makeRain();
g.add(rain);
g.userData.update = (t, dt) => { rain.userData.update(t, dt); };
```

Calling a missing hook throws and can stop the whole scene's animation.
Anything built with `patchStandard` is driven instead by one
`tickShaders(scene, t)` — call it once, in `scene.js`'s `update`.  An effect
whose hook is never called stands still.

**3. Shadows.**  Displaced vegetation casts REAL moving shadows: flags,
banners, reeds and flowers by default; grass and wheat behind `shadows: true`
(tens of thousands of instances in the shadow map is a cost you opt into).  A
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
  albedos belong in **0.02–0.8** linear; a pure black or a pure white albedo is
  always wrong.
* `logarithmicDepthBuffer` is OFF by default, so the library's log-depth chunks
  compile to nothing here.  They cost nothing and `--log-depth` is a host
  option, so leave them alone.
* Everything reads `scene.environment` and the sun direction.  Do not hardcode a
  light colour inside an effect; pass `sunDir` / the rig.

## A crown is leaves, not a lumpy ball

The single most common way a scene reads as plastic is a tree whose foliage is
displaced Icosahedra.  It is not for want of effort — a measured avenue stacked
32 of them per tree with four octaves of noise and still shipped what the
renderer flagged as "a single flat untextured material, an unreadable slab".  A
closed surface has no silhouette to read, lets no light through, and carries ONE
hue however carefully that hue is picked.  `makeCanopy` places a few thousand
separate leaf cards as one draw call instead: on the same tree the hue spread
across the crown went from 3.6° to 9.4° and the 5–95 range from 10 to 22 — a
photograph carries 25–45.  Use it for every broadleaf crown, hedge and bush near
enough for a camera to resolve; `makeImposters` still owns the far treeline.
