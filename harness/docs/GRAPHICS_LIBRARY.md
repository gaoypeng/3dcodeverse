# Graphics libraries and the scene authoring surface

The harness already supplied reusable graphics code before the natural-element
extension. There are two distinct authoring paths:

| Track | Agent-facing reusable code | Execution |
| --- | --- | --- |
| `scene` / `scene_threejs` | `src/lib/*.js`, copied from `codeverse3d/languages/scene_threejs/starter/src/lib/`; factories and shader patches indexed in `codeverse3d/prompts/scene_threejs/effects_catalog.md` | Three.js WebGL scene host, authored cameras, absolute-time scene update, shared post chain |
| `graphics` / `glsl_shader` | Selected cookbook functions and dependencies seeded into harness-owned `src/recipes.glsl` by `tracks/graphics.py` | Fragment shader frames and motion probes through the graphics runtime |
| `graphics` / `opengl_python` | Raw moderngl and GLSL with cookbook guidance | Python-driven OpenGL passes; no Three.js object library |

The scene library is the right place for reusable objects that coexist in a
Three.js world. Imports from the shipped tree are explicitly allowed under the
raw-language contract. The harness freezes those files against agent edits;
the agent composes them from its own scene, zone and environment files.
The dedicated standalone asset-generation stage still permits only Three.js
imports. Instantiate shipped effects in zone/environment code instead of planning
duplicate flame, water-ribbon or grass-patch assets. The scene planner now states
this distinction explicitly; the first live trials exposed that ambiguity.

The initial audit found 44 scene library modules, including procedural
materials, noise, shader composition, grass, reflective water, sky, weather,
terrain, foliage, buildings and placement helpers. This shares its lineage
with a separate reference project outside this repository.
That reference has additional modules, but it is not a drop-in replacement:
the harness has its own renderer, lifecycle and effect budget, and D74 records
the deliberate removal of eight unused effects. Copying every reference
module back would undo that decision.

The first natural-element extension added six modules, expanding the original
44-module library to 50:

| Module | What it adds | Material limitation |
| --- | --- | --- |
| `fire.js` | Local volumetric flames, animated illumination, embers, wax candles with pools, drips and wicks | Transparent-volume ordering requires care; no combustion simulation |
| `ocean.js` | A directional Gerstner spectrum, sampled height/normal, filtered reflection and crest foam | Reflection uses a mean plane; no breaking-wave fluid solver |
| `stream.js` | A descending curved channel, refracted stone bed, flowing ripples, obstacle wakes and optional bank reflections | Opaque-scene screen-space refraction; approximate caustics and mean-grade reflection; no fluid/solid coupling |
| `sand.js` | Seeded dunes, filtered wind ripples, grain detail and moving windborne grains | Dune shape is static; no granular erosion simulation |
| `rock.js` | Closed fracture-plane rock geometry, strata, minerals, moisture and moss | Procedural geometry rather than scanned geological assets |
| `meadow.js` | Multiple leaf growth forms, habitat/age variation, optional seed heads, coherent wind and thin-leaf lighting | Density and shadow resolution must suit the viewing distance |

Every new factory returns a Three.js object with `userData.update(t, dt)` and
`userData.dispose()`. `t` is absolute seconds; update each effect once from the
zone or scene. Sampling APIs operate in the object's local coordinates. The
factory owns its internal resources; externally attached props retain their
own lifecycle. Individual module JSDoc specifies dimensions, options and limits.

The extension reuses existing `noise.js`, `shader.js`, material and lighting
helpers. The shared renderer owns tone mapping and postprocessing; examples
do not replace the renderer or change its global settings. A separately
verified Three r182 shadow-filter compatibility fix is recorded in D97.

See [Graphics Lab](../examples/graphics_lab/README.md) for runnable studies,
stills, deterministic H.264 capture, a live viewer and browser validation.
The hand-composed studies and actual model-generated trials are kept separate,
so a curated demonstration is never presented as an autonomous agent result.

The second refinement replaces the stream's alpha overlay with Three's physical
transmission path (IOR 1.333). `waterColor` now specifies volume absorption over
`attenuationDistance` metres, rather than painted surface colour. Opaque objects
under the water are refracted; transparent objects and offscreen geometry are
outside that screen-space source. `reflectionSize: 0` is the default; a value of
256–2048 adds one planar capture of the banks, and must not be combined with
another water/Reflector capture. The built-in transmission pass has its own
rendering cost even with bank reflections disabled.

Graphics Lab's `compare.py` retains immutable baseline workspaces and replaces
exactly one module per comparison. Its before/after manifest records source
hashes, identical cameras and time, and real GPU metrics. Scene dressing in the
main gallery is separate from that controlled library comparison.

The subsequent full-library review is tracked in [the development record](GRAPHICS_ROADMAP.md).
It includes explicit resource ownership, transformed normal/placement fixes,
point-light shadow silhouettes, patch-preserving material clones and local
physical surface response. Shader-material clones also retain borrowed texture
samplers, including live render targets, and static batches retain source uniform
controls. Added capabilities with inspected GPU compositions include
structural trees, self-shadowed cloud volumes and closed fractured ice. `tree.js`
provides connected oak/birch/willow and shrub architecture with individual leaves,
wind and shadow deformation. Default trees are roughly one million triangles;
use their leaf budget and actual census results for each composition. A bounded
`leafSegments` option reduces distant leaf geometry while keeping the seeded
placement, count, colour and woody structure unchanged. Species defaults retain
the close-view silhouette; four segments suit unresolved background leaves. `cloudvolume.js`
provides a bounded density field with internal sun attenuation, not terrain
shadows or fluid dynamics. `ice.js` builds closed floes with real gaps, local
surface queries and approximate face-dependent optical paths.

`waterfall.js` adds a weir/free-fall sheet with conserved flow thickness,
advected surface detail and existing impact spray. Its origin is receiving
water level, lip at `[0,height,0]`, with flow toward +Z. The caller supplies
the upstream surface, backing/cliff and pool. The CPU query and actual GPU
position/thickness are checked independently. The composed stone/woodland weir
study uses one 512-pixel downstream reflection capture.

`paving.js` adds foreground cobbles and rectangular setts as closed bevelled
geometry with recessed joints. Its exact local height query includes both stone
tops and the joint bed, so props can be seated correctly. It uses two meshes and
a bounded stone count; the material-only cobble preset remains useful at distance.
This is procedural paving rather than photogrammetric stone.

`smoke.js` adds `makeSmoke` and `makeSteam`: seeded, rising volume density with
wind, expansion, dilution, Beer extinction and directional/point illumination.
The emitter is at local y=0; density queries use local coordinates and optical
extinction uses world metres, including nonuniform object scale. Both return a
Mesh with absolute-time update and owned disposal. In ordinary depth mode smoke
and cloud volumes test their first contributing density sample, so an opaque
object behind visible density no longer incorrectly erases the entire volume.
Neither volume integrates scene depth to truncate density at embedded opaque
objects; logarithmic depth retains the proxy-box fallback. No collision solver,
scene-shadow reception or shadow casting is implied. The smoke study includes
tea and charcoal examples with captured source hashes and six-second films.

`firefield.js` adds `makeFireField` for campfires, larger fuel beds and authored
window/roof flames. Up to 16 emitters share one integrated flame/soot field, with
metre-sized embers and at most four practical lights. A baked source atlas keeps
the per-sample density cost independent of emitter count; source layout and wind
are construction-time settings. The balanced tier uses 80 view samples, five
soot-light samples and about 1.49 MB of 3D texture storage, before optional depth
targets. Screen coverage and quality still determine render cost.

An explicit `occluders` list enables a separate borrowed-geometry depth pass and
truncates volume rays at those opaque surfaces. Perspective/orthographic cameras,
offset viewports, transformed fields, camera layers, alpha cuts, instances and
supported depth-material deformation have actual GPU regressions. This resolves
embedded-wall intersections that the older first-density test cannot handle.
Glass and overlapping transparent volumes remain unsupported; skinned occluders,
morphed InstancedMesh occluders and logarithmic-depth capture are excluded.
Unmatched custom vertex deformation needs a corresponding `customDepthMaterial`.
The implementation does not simulate combustion, fluid flow, fire spread or
structural collapse. Legacy `makeFire` and `makeCandle` remain available unchanged
by this addition. See the campfire/bonfire and structural-fire gallery studies.

The GLSL authoring surface also has executable SDF, volume-transport and direct
GGX regressions. Three composed recipe studies and their ordered 144-frame
films are built with `examples/graphics_lab/build_recipes.py --video`. These
are authored examples, with source/recipe hashes, rather than model outputs.

Standalone `.vert`, `.frag` and `.glsl` files also work inside a Three.js scene.
Load their text using `THREE.FileLoader` and a module-relative `new URL(...)`
inside async `createScene`; use the resulting strings in `ShaderMaterial` or
`RawShaderMaterial`. JavaScript still owns object construction, uniforms and
updates. The browser does not support a raw `.frag` ES module import or a bundler
`?raw` suffix here. Arbitrary GLSL helper files must be loaded and joined explicitly;
Three's `#include <chunk>` resolves its own shader chunks, not filesystem paths.
See the [scene contract](../codeverse3d/prompts/scene_threejs/contract.md) for the
loading pattern and GLSL ES conventions. Synchronous asset builders retain their
existing contract; perform loading in the scene factory before returning.

The source writer, snapshots and candidate promotion preserve these shader
files. Shader preflight includes them in source reports and maps GPU compiler
errors to their original file and line. Cross-file uniform declarations and
bindings are checked after material assembly, avoiding JavaScript-only assumptions
about standalone GLSL. Graphics Lab stages and hashes all three shader suffixes
alongside scenes and library modules, including capture and transfer packages.
Desktop OpenGL host code requires a browser/WebGL adaptation; shader-text support
does not make native OpenGL programs directly executable in Three.js.

The final September 23 integration passed **2,922 offline tests** and Ruff,
including the large-fire and external-shader additions. One existing Pillow
deprecation warning remains. This is behavioral/runtime evidence, not a claim
that every procedural effect is photorealistic. Gallery and media results are
recorded separately in the [development record](GRAPHICS_ROADMAP.md) and
[gallery review](../examples/graphics_lab/REVIEW_2026-09-23.md).
