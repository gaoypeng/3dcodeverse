# Shared graphics, water and runtime audit

> Historical working notes from the development snapshot (2026-09-23). The per-module test files
> and case counts cited below were folded into `tests/scene_runtime/lib/test_library.py` when the
> library landed on main (D96, D97); `/tmp/...` evidence paths are from that session and are not kept.

This is the integration audit for the eight original modules assigned to the
core pass, with the later shared-runtime regressions and new capabilities recorded
separately. The authoritative module ledger is
[`inventory.json`](../examples/graphics_lab/output/seven-hour/inventory.json).
Its 50 historical source hashes all match the untouched
[`baseline/src/lib`](../examples/graphics_lab/output/seven-hour/baseline/src/lib/).
At this checkpoint, 47 original modules changed; `caustics.js`, `finish.js` and
`ocean.js` were retained unchanged. Retention is an explicit disposition, not an
omission. There are eight additional modules: seven visual capabilities and one
ownership helper. They are not counted among the original 50. The accepted frozen
source contains **58 modules and 190 actual named exports**. The gallery registry
contains 19 scenes (16 library studies and three independent agent cases), plus
three external shader files.

The [weather](graphics_audit_weather.md), [life](graphics_audit_life.md) and
[surface](graphics_audit_surfaces.md) ledgers explain the other 42 original
modules. The inventory gives each module's actual named exports, baseline and
current SHA-256, disposition, tests, concrete evidence and remaining limits.
The inventory binds the accepted frozen sources and completed integration evidence.
The final offline suite, gallery captures, strict media checks, browser controls and
maintained first-six transfer package pass, with the limits recorded below.

## Original core modules

| Module | Final disposition, concrete evidence and limits |
| --- | --- |
| `shader.js` | **Corrected shared behavior and ownership.** Surface normals now use the inverse transpose, including mirrored transforms; local directions use the full reciprocal basis under composed shear. Sixteen model/instance combinations are compared to CPU references on the GPU. Displaced point-light distance shadows now accompany depth shadows, with shared alpha silhouettes. Normal and transmission hooks preserve patch order. Depth-pass guards compose existing callbacks and restore authored draw ranges. Typed material cloning preserves borrowed samplers, control sharing and registered patches. The shared synchronous `withRendererState` scope now restores nested capture state on success and failure. See `test_shader_geometry.py` and the detailed regression table below. Arbitrary external shader wrappers and host postprocessing remain outside this helper's contract. |
| `place.js` | **Corrected world/local placement.** The frozen valid scaled-parent fixture buried a seated base by 16.5506 m, moved it horizontally by 7.8634 m and missed the requested heading by 94.18 degrees. Seating, facing and route placement now preserve world targets; hidden ancestors cannot supply supports or obstructions. `test_spatial_ownership.py` measures resulting positions and headings. Baseline measurements are retained in `/tmp/graphics-seven-hour/baseline/core/probes.json`. These are placement heuristics, not rigid-body collision simulation. |
| `instancing.js` | **Corrected geometry and cleanup.** A mirrored baseline box had all 12 triangles wound against its normals, and a hidden sibling was baked into the result. Prototype mirrors now reverse winding; mirrored placements use a corrected batch with positive-determinant matrices. Hidden branches are omitted. Cleanup owns copied geometry and instance buffers while borrowing prototype materials. Invalid zero/infinite spacing fails without unbounded construction. Evidence: `test_instancing.py`, `test_spatial_ownership.py` and the frozen core probes. Batching still depends on compatible prototype materials and geometry. |
| `merge.js` | **Corrected material preservation and cleanup.** Batches previously replaced physical materials with MeshStandardMaterial, losing transmission, thickness, IOR and attenuation. Cloning now retains the material type and registered patches, with deliberately shared animation controls. Mismatch diagnostics cover optical maps/properties, and cleanup owns only output geometry/material. Existing colour baking and mirrored winding remain. `test_merge.py` and the live render-target clone/merge regression verify behavior. Merging is not a substitute for transparent-object sorting. |
| `environment.js` | **Corrected enclosure geometry and ownership; retained background appearance.** Independently built sill/lintel strips refilled overlapping door/window openings. The enclosure now subtracts their union, checked by more than 1,500 occupancy rays in `test_room_shell.py`. Environment factories dispose their own maps and light shadow targets; caller attachments stay borrowed. `test_environment.py` and spatial ownership tests cover the contracts. The inexpensive horizon, ground continuation and mood lighting remain lighting/background tools, not volumetric weather. |
| `noise.js` | **Corrected numerical limits and retained field appearance.** The seed-offset cache is bounded without changing deterministic values; zero octaves produces a flat field and nonfinite/overflow inputs fail clearly. Displacement refreshes existing culling bounds. `test_noise.py` and spatial ownership checks cover these changes. The established seeded ImprovedNoise field remains, avoiding changes to every dependent effect's geometry. It is a procedural field, not a measured physical distribution. |
| `ocean.js` | **Retained unchanged after prior visual refinement.** Its existing implementation has 32 dispersive directional bands, analytic normals, filtered micro-slope roughness, directional sky reflection and bounded mean-plane reflection. The ten current tests cover inverse surface sampling, narrow shore contraction, light refresh, ownership and GPU compilation. Prior fixed-camera images are in `examples/graphics_lab/output/comparison/renders/ocean/after/`; the refined first-six package includes the film. Those visual changes predate this seven-hour pass and are not counted again. Limits remain non-overturning kinematic waves, one mean-plane reflection and no fluid solver. |
| `stream.js` | **Corrected composed optics and reflection state; retained recent flow design.** Border fallback blends radiance rather than folding UVs. Optical rays intersect the graded bed under full world/local transforms, and optional endpoint fade supports weir joins. The reflector uses an inverse-transpose world-plane normal under scale/shear, and scoped cleanup restores renderer state on success or failure. The 15 current cases include actual refraction/border pixels, transformed optical rays, mean-plane camera geometry and GPU state restoration. The waterfall black patch was traced to upstream meadow NaNs, not repaired by hiding the sheet. Matched evidence and limits are recorded below. |

## Shared regressions checked against the actual tests

These are current collected cases, including parameter expansion. Counts identify
coverage. The later planar and external-shader follow-ups have retained execution
logs and real GPU evidence. Historical family execution results and final combined
integration status are distinguished below.

| Component and test file | Cases | Behavior demonstrated |
| --- | ---: | --- |
| `tests/scene_runtime/lib/test_shader_geometry.py` | 8 | Inverse-transpose normals and reciprocal directions cover 16 model/instance combinations. Depth/distance variants share deformation, time and alpha silhouettes. Callback guards support arrays, later material replacement and authored ranges. Physical transmission hooks execute after maps and survive cloning. ShaderMaterial/RawShaderMaterial clones retain ordinary and render-target texture identity while copying typed values, unless sharing is explicit. A real GPU check samples a live render target through a clone and a merged batch, including later source-uniform edits. |
| `tests/scene_runtime/lib/test_meadow.py` | 6 | Fractional blade-height powers use a finite domain. A 3,000-blade, four-sample, half-float target with NoToneMapping is read back at two times; the failing fixture had 699 nonfinite HDR channels and the corrected fixture has zero. Other cases retain seeded geometry, motion, validation and ownership coverage. |
| `tests/scene_runtime/lib/test_stream.py` | 15 | Actual checkerboard refraction and border-radiance tests accompany CPU/GPU flow and sampling checks. Independent ray/plane calculations cover transformed optics. Reflection camera geometry covers scale/shear. Eight renderer-state configurations cover pixel ratios 1/2, default versus post-binding target rectangles, and successful versus injected-failure capture. |
| `tests/scene_runtime/lib/test_planar_capture.py` | 3 | Water and wet-ground addon capture cameras and projected coordinates agree with independent plane math under uniform, nonuniform, mirrored and composed transforms. Sixteen success/failure target configurations preserve renderer state. Caller-attached Mesh, Camera/inverse, Bone hierarchy and manually managed world matrices survive nested capture and return with their authored update flags; disposal preserves caller resources. The third test checks nested shared-state scopes and callback results/exceptions. |
| `tests/scene_runtime/test_external_shaders.py` | 8 | Actual GPU loading of separate `.vert`, `.frag` and `.glsl` files succeeds; a broken raw fragment maps to its actual source line. Raw shader static auditing does not reinterpret helper text as an independent JavaScript material. A successful GPU compile resolves uncertain declarations only when the audited literal participates in the evaluated shader and its source origin is unambiguous. Three additional regressions preserve errors in unused/ambiguous bodies; missing runtime bindings remain errors. |
| `tests/scene_runtime/test_census_instances.py` | 3 | The bound `geometry._maxInstanceCount` is authoritative. An unused short instance attribute cannot reduce the actual draw count. GPU submissions with requested instance counts 8, 3 and Infinity agree with renderer statistics; pre-binding inference remains conservative. Hidden ancestors, draw-range starts and primitive type are covered. |
| `tests/graphics/test_graphics_lab_lifecycle.py` | 1 browser test, six scenarios | Authoritative disposal, fallback ownership boundaries, fallback cleanup failure, throwing scene/renderer cleanup, invalid scene contracts and cancelled asynchronous creation all release renderer/context/canvas exactly once. Borrowed textures, cached materials and caller attachments survive. Cleanup failure cannot prevent later cleanup steps. |

The shader clone repair addresses two distinct native-clone failures: ordinary
textures were duplicated, and render-target uniforms could become null. Aliasing
between `material.uniforms` and the library control map is preserved. Registered
Astra patches are copied; an arbitrary caller's `onBeforeCompile` wrapper still
requires explicit reattachment. The tests check runtime values and rendered pixels,
not merely shader source spelling.

Scene census counts visible submitted geometry. It does not multiply that count by
shadow, reflection, transmission or postprocessing passes, and points/lines do not
become triangle counts. Viewer fallback cleanup observes ownership boundaries;
an explicit scene disposer remains authoritative rather than being followed by an
unconditional resource traversal.

## Waterfall composition: isolated failure and repair

The stream investigation is frozen at
`/tmp/graphics-seven-hour/stream-reflection-fix/README.md`, with before sources,
an isolated clamp-only variant, exact final sources/tests, `comparison.jpg` and
`sha256.json`. The matched waterfall view used a 512-pixel reflection target.
In the fixed region x390:510, y475:525, near-black pixels fell from 793 to zero.

Captured half-float transmission and reflection targets established the cause:
multisample edge interpolation made a meadow height coordinate slightly negative;
fractional powers generated NaNs. The falling film sampled that invalid HDR
radiance and mip filtering spread it. Hiding the film removed propagation but left
the bad upstream target. Clamping only the meadow power inputs removed the visible
patch with the film, its transmission and stream reflection all enabled. This is
supported by the separate meadow HDR regression; it is not a claim that every
black pixel is a transmission defect.

The stream's independent plane-transform repair agrees with an analytic reflected
camera within 2.2e-9 m in its fixture; the 1e-7 tolerance permits the Float32 ribbon
anchor. Capture restores target, cube face, mip, logical and actual viewport/scissor,
scissor test, XR, shadow auto-update, tone mapping, clear colour/alpha and prior
surface visibility. Post-binding rectangle overrides are retained without changing
target metadata. A `finally` block covers errors in capture; it cannot reconstruct
a partially rendered frame after arbitrary errors inside Three.js.

The earlier border-only comparison remains at
`/tmp/graphics-seven-hour/new/stream-edge-comparison/`: only `stream.js` changed,
with six matched GPU frames. Sixteen transformed optical-ray fixtures agree with
independent bed-plane intersections within 0.012 mm at these scene scales.
The later final-b reflection evidence contains eight frames at four matched cameras
and times 0/1.5, with no shader/console errors; all 15 stream cases passed in its
recorded checkpoint. One mean-grade reflection and screen-space transmission remain
approximations, without recursive transparent transport or hydraulic collisions.

## Addon planar capture maintenance follow-up

`/tmp/graphics-seven-hour/planar-capture-review/README.md` freezes the Water and
wet-ground transform/state repair. Stock addon rotation extraction cannot represent
the inverse-transpose normal under shear/nonuniform scale. A temporary rigid plane
frame supplies the correct reflected camera, while each addon retains its own
projection convention: Reflector's matrix is remapped to actual mesh-local geometry;
Water's matrix projects world positions and is not remapped. Identity-scene RGBA
readback stayed bit-identical in 1,228,800 channels. Thirty-two matched full-post
frames cover water, wet ground, rain and cloud scenes with no compile/console errors;
the retained repeated-baseline controls quantify minor postprocessing variation.

The shared `withRendererState` guard restores target/cube face/mip, logical and
actual viewport/scissor, scissor test, XR, shadow update, tone mapping and clear
colour/alpha. Foreign override/material passes and capture re-entry are skipped.
The addon mesh's real world matrix and update flags are restored after capture.

Independent review then found that the nested scene update propagated the temporary
rigid matrix to caller descendants. `/tmp/graphics-seven-hour/planar-descendant-review/`
retains exact before/after sources and a regression that fails both old factories.
The final fix freezes descendant world recomputation for the capture and restores
both authored update flags in `finally`. Mesh, Camera including its inverse matrix,
Bones and manually managed transforms are checked during and after actual rendering,
on success and injected failure. Camera/bone ownership remains with the caller.
The relevant 48-case suite passed in 20.43 seconds; the independent reviewer also
reproduced the fix on the GPU. Ordinary horizontal calm-water normal fields remain
an approximation: correct reflection geometry does not imply arbitrary-orientation
physical water shading, a multiple-reflector coordinator or recursive transport.

## Eight accepted additions, kept separate from the original fifty

| New module | Capability, evidence and explicit limits |
| --- | --- |
| `firefield.js` | `makeFireField` combines 1–16 authored sources into a bounded flame/soot field with a baked source atlas, rising reaction sheets, world-distance optical extinction, sized embers and practical lights. **Ten tests pass.** `/tmp/graphics-seven-hour/firefield-final/` freezes the accepted SHA `bbb4c77b1e4b88f23f9cdc34d88d3b2f428b510ea2c48422546328ff789c1fff`, nine structural-fire GPU views, and exact sources. Optional isolated borrowed-geometry depth proxies terminate rays at opaque geometry; perspective/orthographic, transformed cameras, hidden parents/layers, opacity/alpha cuts and dynamic displacement are checked. Camera-layer, inactive-emission, displacement-refresh and opacity defects were independently reproduced, repaired and rechecked. This is prescribed fire/soot transport, not combustion, fuel consumption, ventilation, fluid dynamics or structural failure. Skinned/morphed-instanced occluders and optional log-depth capture are unsupported; transparent intersections remain limited. |
| `cloudvolume.js` | Bounded ray-marched clouds, self-shadowing, Beer extinction, quality tiers and a CPU density query. **Seven tests pass.** `/tmp/graphics-seven-hour/cloud-final-refinement/` is the accepted latest source and 48 matched composed/independent-seed GPU images. Stationary unequal convection towers and support prevent advected noise regrowing against proxy faces; G/B erosion noise still moves. CPU/GPU inset-boundary density falls from about 0.028 to zero for the regression. Earlier first-density depth and rounded base-fade repairs remain. This does not simulate evolving convection, truncate rays at intersecting opaque geometry, or cast terrain cloud shadows; log-depth retains the proxy fallback. |
| `ice.js` | Closed bevelled fracture floes, fitted gaps, absorption/frost, internal air lenses and an exact top-triangle height query. Nine tests and `/tmp/graphics-seven-hour/ice-final/` provide source and three-view evidence. Optical exit distance is approximate; Three transmission does not recursively trace water or other floes. Fractures are authored, not a mechanics simulation. |
| `paving.js` | Closed crowned/bevelled cobbles or setts, recessed joints, moisture response and triangle-based height sampling in two merged meshes. Eight tests and `/tmp/graphics-seven-hour/paving-final/` plus `paving-variants/` cover geometry and dry/wet appearance. Patches are rectangular, without arbitrary-route conformity or standing-water optics. |
| `smoke.js` | Seeded rising smoke/steam, bounded density, CPU query, quality tiers, lighting and world-metre extinction. Six tests include nonuniform optical path scale and first-density depth testing. Frozen evidence includes `/tmp/graphics-seven-hour/new/smoke/renders-v7-dilution/` and `new/smoke-v7-films/`. These are prescribed transport/scattering fields, without fluid dynamics or scene-depth truncation; logarithmic depth keeps the proxy fallback. |
| `tree.js` | Connected branches, petioles and leaves, species-specific forms, coherent wind and filtered bark. **Sixteen tests pass**, including finite HDR and supersampled bark for three species. `/tmp/graphics-seven-hour/tree-bark-refinement/final/` freezes the material refinement. Later opt-in `leafSegments` reduces leaf triangles while preserving wood, leaf count, placements and colours; species defaults stay unchanged. Rain V5 uses four segments for background leaves: 20,706,817 to 12,419,137 scene triangles (40.02% less), with the same 102 meshes and 490,979 instances. This is a geometry reduction, not an FPS claim. Wind is prescribed and host AO sees rest geometry. |
| `waterfall.js` | Accelerating free-fall film with width/velocity/thickness conservation, advected breakup, local aeration and existing impact spray. Four tests compare CPU/GPU film position and thickness and cover quality, replay and ownership. Current composed evidence is `/tmp/graphics-seven-hour/waterfall-composition/final-reflective/`, with the lower stream reflector enabled at 512 pixels; the earlier `new/waterfall-v9-films/` films retain their own source versions. This is an authored sheet, not hydraulic collision simulation; its transmission sees the opaque scene and impact spray is not simulated droplets. |
| `lifecycle.js` | **Support module, not a visual effect.** Construction-time resource snapshots and idempotent, failure-tolerant cleanup. Two direct tests and family ownership regressions verify the contract. Textures are not implicitly owned; factories must add allocated textures and other private resources explicitly. |

The accepted cloud support refinement retains the final-b depth/base repairs and
supersedes the earlier five/six-test cloud checkpoints. The later meadow HDR repair
and tree bark refinement likewise supersede those specific life-family snapshots. Earlier immutable evidence
remains useful but is not silently relabelled as the latest source.

The separately composed campfire/open-bonfire V4 study retains nine accepted GPU
frames under `/tmp/graphics-seven-hour/campfire-root-v4/`. The shared field's bounded
local emitters, smoke, embers and practical lights scale independently; legacy
`fire.js` remains unchanged by this extension. The structural scene is visibly
procedural, and fine upper-wisp ray-march grain and saturated bright windows remain
honest visual limits. Whole-image motion includes other animated scene content;
the dedicated regression isolates field motion and exact absolute-time replay.

The independent integration review is retained at
`/tmp/graphics-seven-hour/final-integration-review/README.md`. In addition to the
four firefield issues above, it records repaired external-GLSL declaration handling,
a package verification-to-copy race, and planar descendant transforms. Corrections
have independent rechecks; this does not replace the final combined suite/gallery.

## Separate graphics authoring surface

The GLSL cookbook is executable library content seeded into `src/recipes.glsl`.
Its star distance function, zero-length segments, reversed smoothstep edges and
rain-cell advection were corrected using numerical GL checks. Bounded-volume
transport and direct GGX recipes provide explicit conventions and dependency
seeding; they are neither a fluid simulator nor a complete path tracer.
The OpenGL cookbook's sphere winding now agrees with its outward normals, and its
normal/ownership guidance matches the actual setup/render host contract.

Three six-second GLSL films are retained under
`examples/graphics_lab/output/seven-hour/recipes-motion-v1/`, with source hashes,
ordered frame verification and a recorded mean compression error of 1.03–1.48/255.
The explicit frame order prevents lexical `f100`-before-`f10` encoding. A real
relative-output render exposed double resolution of the GL job path; resolving
the output directory once fixed it. These recipes and runtime fixes are additional
work, not extra JavaScript modules in the 50-plus-seven census.

The Three.js scene authoring contract now explicitly supports local shader source
files through `FileLoader(loaders.manager)` and module-relative URLs in async
`createScene`. Browser loading remains outside synchronous asset builders. The
accepted `external_shader.js` study loads a `.vert`, `.frag` and `.glsl` helper,
uses explicit GLSL3 interfaces and animates cached uniforms/transforms at absolute
time. `/tmp/graphics-seven-hour/external-shader/` retains three camera views at
three times, clean actual-GPU shader diagnostics, zero nonfinite HDR channels under
4x MSAA, exact time replay and exactly-once owned-resource disposal. Its restrained
alloy finish uses an analytic studio reflection field; it does not receive scene
shadows/interreflection and is not a multilayer optical solver. Gallery source
staging, hashes and portable packaging are separate integration checks.

## Final verification and retained history

The final whole offline suite is **clean: 2,922 passed, one existing Pillow
deprecation warning**, in 225.65 seconds. The retained dedicated-cache log is
`/tmp/graphics-seven-hour/offline-final-clean.log`. Ruff also passes, retained in
`/tmp/graphics-seven-hour/final-ruff-clean.log`.

The completed [gallery V2](../examples/graphics_lab/output/seven-hour/gallery-v2/index.html)
contains **19 cases, 53 cameras and 159 stills**, with no shader, console or update
errors in the retained render metrics. Its 19 primary films and 11 extra views
produce **30 verified films and 4,320 decoded frames**. The
[strict media report](../examples/graphics_lab/output/seven-hour/gallery-v2/media-verification/report.json)
checks every encoded digest, three retained encoding-input PNGs per film and all
63 source files per staged scene. Twenty-seven films have measurable anchor-motion
masks; rocks, sand and workshop remain below that threshold without failing.
Maximum first-frame RGB error is 4.688922882/255; maximum 160×90 input-anchor error
is 1.316990733/255. Motion/error metrics verify media correspondence, not realism.

The [browser report](../examples/graphics_lab/output/seven-hour/gallery-v2/browser-check.json)
passes all 19 cases and 53 cameras, playback/pause/scrub/reopen/cancellation controls,
with zero errors. Its English scan covered 4,407 files before the final transfer
package was added; this statement does not substitute for a later expanded scan.
The local gallery is served at [http://127.0.0.1:45649/](http://127.0.0.1:45649/).
The water/atmosphere still review is retained in
`/tmp/graphics-seven-hour/final-water-gallery-review.md`: 15 selected final views
match accepted work without a material capture regression. Rounded cloud billows,
faceted terrain, the interior terrain edge, prescribed water motion and other
explicit procedural limits remain visible.

The maintained first-six transfer is available as a
[folder](../examples/graphics_lab/output/seven-hour/gallery-v2/packages/graphics_lab_first_six_maintained_2026-09-23/)
and [ZIP](../examples/graphics_lab/output/seven-hour/gallery-v2/packages/graphics_lab_first_six_maintained_2026-09-23.zip).
It contains 113 files; the ZIP is 74,973,979 bytes with SHA-256
`26efcebd51c76ba7b1a27eb8fdeea5cab0851ec0b9ef628bfaf556f3ae501098`.
The source manifest, ZIP CRC and extracted hashes pass, recorded in
`/tmp/graphics-seven-hour/final-transfer-package.json`. This package covers fire,
ocean, stream, sand, rocks and meadow; the full 19-case gallery is a separate
local output. Earlier delivered packages remain immutable.

Historical attempts remain explicit. Gallery V1 is **failed and retained**, and
is not a completed deliverable. `/tmp/graphics-seven-hour/offline-integration-1.log`
records 2,872 passes and the subsequently repaired documentation-map failure.
`offline-maintenance-checkpoint.log` records the clean but earlier 2,890-pass
maintenance checkpoint. `offline-final-integration.log` records 2,917 passes and
two failures: the cloud boundary fixture's browser closed with `Target closed`,
and an unused `bad.js` declaration error was wrongly cleared after an unrelated
starter shader compiled. Despite that test's historical `without_compile` name,
preflight compiled the starter. The final correction requires literal participation
in the evaluated shader and unambiguous source origin. The retained diagnostic
checkpoint (`shader-report-focused.log`) has 29 passes; the isolated cloud recheck
(`cloud-recheck-isolated.log`) has seven passes in 18.97 seconds with no source
relaxation. The clean 2,922-pass final run supersedes the failed attempt. All these
log names resolve under `/tmp/graphics-seven-hour/`; none is relabelled as another run.

The previous collection preserves 742 cases across 67 files in
`/tmp/graphics-seven-hour/historical-final-inventory-test-collection.txt`.
The final collection contains **768 cases across 70 files** in
`/tmp/graphics-seven-hour/final-inventory-test-collection.txt`, including library,
census, viewer lifecycle and external-source tests. Actual module imports verify
190 named exports, and all 58 library hashes match the frozen canonical files.
Collection remains distinct from execution. Family checkpoint counts overlap and
must not be summed into the final pass count; their snapshots prove the exact
captured module versions, not the latest identity of all shared dependencies.

Rain V5 is a composition change, not an isolated library comparison.
`/tmp/graphics-seven-hour/rain-composition/REVIEW.md` and `leaf-detail-comparison.json`
record its fixed-camera appearance and background leaf reduction. The earlier
shelter probe remains applicable because later planting revisions leave roof,
deck, rain and splash construction unchanged: 4,092 deck probes per camera have
zero roof misses, and all 720 splash positions avoid sheltered ground impacts.
Repository source/tests and the final gallery reports are durable references;
`/tmp` studies are local unless explicitly packaged. This ledger does not claim
that every historical temporary capture is included in the maintained six-case ZIP.
