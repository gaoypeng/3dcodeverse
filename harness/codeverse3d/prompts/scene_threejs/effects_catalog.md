# The shipped effect library — call these before writing your own

`src/lib/` is in your workspace already: 44 modules, every one of them compiled
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
| a grassy surface | `makeGrass({ extent, density, height, heightAt, patchy })` — `lib/grass.js` |
| A CROWN OF LEAVES — every broadleaf tree, hedge and bush | `makeCanopy({ crowns: [{position, radius, height}] — or [{position, size:[x,y,z]}] for a mass that is not a ball, which is how a HEDGE is built — color, hue, wind })` — `lib/canopy.js`.  NEVER a displaced sphere: see the rule at the end. |
| trees, bushes, leaves | `patchLeafSSS` · `patchWind` · `patchRootContact` — `lib/foliage_shade.js` |
| bark, and a forest at distance | `patchBark` · `makeImposters` — `lib/woodland.js` |
| light through a canopy | `patchDappledLight` · `patchCanopyShade` — `lib/dapple.js`; goes on the surface being LIT, not on the tree |
| flowers, drifting petals or leaves | `makeFlowers` · `makeFalling` — `lib/flowers.js` |
| midges, butterflies, fireflies; reeds in water | `makeInsects` · `makeReeds` — `lib/smalllife.js` |
| birds or fish in motion | `makeFlock({ count, kind, extent, height, path })` — `lib/flock.js` |
| a sizeable water body | `makeOcean(w, d, { sunDir })` — `lib/water.js`; ONE reflective surface per scene |
| mist over moving water, spray where it lands | `makeWaterMist` · `makeSpray` — `lib/watermist.js` |
| a waterline | `patchShoreWet` · `patchShoreFoam` · `patchShallowWater` — `lib/waterside.js` |
| anything BELOW the water, rain rings, thin ice | `patchUnderwater` · `makeRainRings` · `patchThinIce` — `lib/submerged.js` |
| the moving net of light under water | `patchCaustics(material, { level, sunDir })` — `lib/caustics.js`; goes on the surface being LIT, not on the water |
| moss, damp near water, dried mud | `patchMoss` · `patchMoisture` · `patchCrackedMud` — `lib/damp.js` |
| a polished or wet floor | `makeMirrorFloor(w, d)` — `lib/wetground.js`; spends the same one-RTT budget as `makeOcean` |
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
| a flag, a hanging banner, a wheat field | `makeFlag` · `makeBanner` · `makeWheatField` — `lib/cloth.js`; the wave TRAVELS |
| a surface that has stood somewhere | `patchDripStains` · `patchRust` · `patchDust` — `lib/aging.js` |
| any surface at all | `patchMicroBreakup` · `patchEdgeWear` — `lib/surface_wear.js` |
| paper, wax, jade, petals; oil film, beetle shell | `patchTranslucency` · `patchIridescence` — `lib/finish.js` |
| a person | `figure({ height, fem, rand })` · `sit` · `walk` · `carry` — `lib/figure.js`; a pose is one of those three calls on the figure, not an option — left alone it stands |
| the same asset hundreds of times, in one draw call | `instanceAsset` · `scatterGrid` — `lib/instancing.js` · `mergeStatic(meshes)` — `lib/merge.js` |
| a textured standard material without a texture file | `brick` · `granite` · `cobble` · `asphalt` · `weatheredWood` · `brushedSteel` · `fabric` · `foliage` · `soil` · `skin` · `glass` (21 in all) · `tint(mat, variant)` — `lib/materials.js` |
| noise on the CPU (heightfields the shader must agree with) | `fbm2` · `fbm3` · `mulberry32` · `displaceY` — `lib/noise.js` |
| your own shader, safely | `makeShaderMaterial` · `patchStandard` · `shadowLike` · `tickShaders` — `lib/shader.js` |

## The four rules that apply to all of them

**1. `patch*` entries CHAIN.**  Apply as many as a surface deserves to one
material, in any order — they are composed into one program.  Two patches may
not define the same GLSL helper name with different bodies; the library prefixes
its own (`astra…`), so prefix yours.

**2. A `make*` entry that moves carries its own per-frame hook —
`userData.tick(t, dt)` on most, `userData.update(t)` on `makeRain`,
`makeSplashes`, `makePuddle`, `makeClouds`, `makeCirrus`, `makeOcean` and
`makeRainRings` — and OUR contract drives it from `update(t, dt)`, not from a
`tick` of your own.**  Fan out in `scene.js`:

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

Calling `.tick` on one of those seven throws, and the host then turns
`update()` off for the whole scene — every animation in it freezes.
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
