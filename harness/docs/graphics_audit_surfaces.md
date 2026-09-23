# Graphics library audit: surfaces and built environments

> Historical working notes from the development snapshot (2026-09-23). The per-module test files
> and case counts cited below were folded into `tests/scene_runtime/lib/test_library.py` when the
> library landed on main (D96, D97); `/tmp/...` evidence paths are from that session and are not kept.

Audit date: 2026-09-23, starting at approximately 07:27 UTC. This is a read-only assessment of the current worktree, including its uncommitted refinements. No shipped module, gallery source, video or delivered package was changed during this phase.

## Evidence and scope

All 15 assigned modules and their exports/tests were inspected. The focused suite passed **185 tests in 13.10 seconds**:

```bash
python -m pytest tests/scene_runtime/lib/test_{aging,building,finish,materials,neon,roadway,rock,sand,signage,strata,surface_wear,terrain,terrain_shade,urban,windows}.py -q -n 2 -m 'not live'
```

The frozen source and evidence directory is `/tmp/graphics-seven-hour/baseline/surfaces/`:

- `src/lib/`: complete baseline library snapshot; `source_hashes.json` records every module digest.
- `src/scene.js`: material palette, metallic weathering and combined terrain/road study.
- `renders/material_close_t0.png`, `material_palette_t0.png`, `metal_weathering_t0.png`, `terrain_road_t0.png`: real 1440 × 810 GPU frames.
- `architecture/src/scene.js` and `architecture/renders/`: cottage, block, tower, window interiors, curtain wall, billboard and power-line study; three 1440 × 810 views.
- `finish/` and `neon/`: staged GPU examples derived from existing test fixtures. The neon staging moves the tubes in front of the wall; the original compile fixture hides them behind it. This fixture correction does not change library code.
- `reproductions.json`: numerical option, patch-cloning and surface-sampling failures described below.

The production host ran on the RTX 5090 Laptop GPU with its normal post-processing chain. Material/terrain, architecture and finish renders reported zero console/shader errors. Neon also renders at t=0 and t=1. Images were visually inspected; passing shader tests is not treated as proof of realism. Initial fixture imports through an external symlink were rejected by the workspace server; copying the frozen libraries into those workspaces resolved that staging issue.

## Highest-value corrections

### P1: weathering must change the local BRDF, not the entire material

`aging.js:126,267,385`, `surface_wear.js:217`, `roadway.js:208,413,533` and `strata.js:396` retain whole-material `composeRoughness` adjustments. Several comments still describe per-pixel roughness/metalness as unreachable. Those hooks now exist in `shader.js:726,732`, and `shader.js:825` explicitly recommends them for partial coverage.

Reproduction: applying full rust to metalness 1 / roughness 0.2 leaves metalness at **1** and changes roughness everywhere to **0.32**. Setting the live `uRustAmt` uniform to zero leaves roughness at **0.32**. A clean region therefore loses its original finish, an oxide patch continues reflecting as metal, and animating coverage does not restore the original physical response. The GPU weathering frame shows this limitation on identical cylinders.

Fix: use the same spatial mask for colour, roughness, metalness and bounded surface-normal relief. Preserve clean substrate values exactly; rust and dust should become dielectrics only under their masks. Add final-frame comparisons under a moving specular highlight and a uniform-zero regression. Existing tests which assert global roughness multiplication encode the old limitation and must be deliberately replaced, not blindly preserved.

### P1: wrong transformed normals affect several whole families

`shader.js:777` (`worldBody`) transforms a normal with `modelMatrix` and `instanceMatrix`, rather than their inverse transpose. Direct copies also occur in `roadway.js:71`, `neon.js:454` and `urban.js:71`. Position transforms are correctly included; the problem is specifically normals under nonuniform scale.

For a 45-degree normal and scale `[4,1,1]`, the helper produces approximately `[0.970,0.243,0]` while the correct result is `[0.243,0.970,0]`. That reverses the interpretation of a nearly horizontal surface into a steep slope. Snow, dust, erosion, triplanar weights, edge wear and spill lighting can all be affected.

Fix centrally with a shared normal-transform helper, including instances. Verify GPU equivalence between scaling geometry in advance and applying the equivalent object/instance transform. Coordinate this with the shared-shader owner; fixing only one material family leaves contradictory surface masks.

### P1: material names currently promise structures they do not generate

`materials.js:253–300` makes brick, cobble, fabric, granite and most other presets from the same generic value-noise texture builder. Only palette, noise scale, streak strength and scalar PBR values distinguish them. The close GPU palette confirms that brick has no courses/mortar, cobble has no individual stones, and fabric has no woven structure. These are coherent noise swatches, not reusable close-view material models.

Fix selected existing factories before adding parallel aliases: periodic masonry courses and recessed mortar; irregular cobble cells with physically scaled joints; filtered warp/weft normal and roughness structure; multi-scale wood grain and pores. Keep caching and deterministic variation, with explicit metre scale and an opt-out for generic distant materials. Use close and grazing renders, plus seam/scale tests.

### P1: seating functions disagree with their actual meshes

`terrain.js:55–69` returns the continuous field while rendering a triangulated approximation. `terrain.js:253` similarly returns analytic cliff depth instead of the rendered triangle intersection. The current tests establish agreement at vertices, which does not establish agreement between vertices.

Measured with raycasts at 200 deterministic positions:

| Configuration | Maximum absolute error | Mean absolute error |
| --- | ---: | ---: |
| Ground: size 100, relief 6, scale 12, 8 segments | 2.677 m | 0.834 m |
| Same ground, 32 segments | 0.899 m | 0.205 m |
| Same ground, 128 segments | 0.166 m | 0.017 m |
| Cliff: length 20, height 8, relief 2 | 0.783 m | Not aggregated |

Fix with barycentric interpolation of the actual grid triangles, while retaining the analytic field privately for construction. `sand.js:123` already implements the appropriate pattern and its current contract should be preserved. A normal sampler would also let props sit flush with inclined surfaces.

### P1/P2: material option and composition defects

- **Confirmed option bug:** `materials.js:241–248` unconditionally restores `bumpMap` when `repeat != 1`, overriding `bump: 0`. The reproduction returns `bumpZero=true` and `bumpZeroRepeat=false`.
- **Confirmed composition loss:** `materials.js:379` (`tint`) clones materials without preserving `onBeforeCompile`; a previously patched material loses its procedural shader. `weather()` has the same clone path for shared materials. `windows.js:379` knowingly clears an existing shared material's patch chain before adding windows. Decide on one supported clone-before-patch or patch-aware-clone contract, then test actual compiled output. Do not silently advertise arbitrary chaining through these mutators.
- **Sharp-edge limitation:** `surface_wear.js:238` estimates curvature from interpolated normal derivatives. Hard BoxGeometry faces have constant normals, so the effect cannot reliably discover their geometric edges. A bevel/curvature attribute or an explicit edge-distance input is needed for architectural edge chipping; a larger strength parameter is not a remedy.

## Per-module disposition

Priorities: P1 fixes materially incorrect behaviour or a major reusable realism gap; P2 is a bounded enhancement; keep means retain the working capability while addressing shared fixes.

| Module and exports | Current evidence / limitations | Disposition |
| --- | --- | --- |
| `aging.js`: `patchDripStains`, `patchRust`, `patchDust` | 11 tests; gravity-oriented, seeded, multi-tone masks are already implemented. GPU rust is a metallic stain; local mask does not control BRDF. Live strength does not undo global roughness. | **P1:** local dielectric/roughness/normal masks. Preserve spatial logic and explicit sill `from`. |
| `building.js`: `block`, `casement`, `cottage`, `tower`, `roofClutter`, `cityFabric` | 34 tests cover real openings, reveals, glazing, anchoring, dormers, budgets and district layout. GPU cottage/block geometry reads clearly. Roof remains a smooth mottled terracotta sheet; wall structure inherits generic material shortcomings. | **P2:** tile/shingle relief and intentional construction details. Keep opening, placement and merge architecture; improve underlying materials first. |
| `finish.js`: `patchTranslucency`, `patchIridescence` | 13 tests including real chained shader compilation. GPU thin shell lights successfully; film produces coloured interference-like regions. Iridescence is an analytic light/colour approximation, not a proper layered physical coating, and documentation recommends reducing metalness to make it visible. | **P2:** evaluate Three Physical iridescence/coating support for reflective metal, keep the current lightweight thin-transmission option. Add shadow/occluder and angular tests before stronger physical claims. |
| `materials.js`: 16 opaque preset factories; `water`, `glass`; `noiseTexture`, `weather`, `tint` | 11 tests verify cache, colours, tiling and dielectric flags. GPU palette exposes missing material-specific structures. Repeat/bump and shader-clone defects reproduced. Glass uses alpha transparency; the water helper is a simple legacy surface. | **P1:** targeted structured materials and option/composition fixes. Preserve caching. Do not confuse legacy `water()` with the richer ocean/stream modules. |
| `neon.js`: `makeNeonTube`, `patchNeonSpill`, `makeLightTrails` | 16 tests, tube transport frames, time-driven gas variation and depth-pass exclusions exist. Source fixture's tubes were hidden behind its wall; baseline staging corrects that. Spill has the nonuniform-normal bug. Animated factories expose legacy `tick`, not the newer `update`/owned-dispose contract. | **P1 shared normal fix; P2** lifecycle aliases and owned cleanup. Keep tube geometry and colour budgeting unless matched visible renders justify changes. |
| `roadway.js`: `patchRoadSurface`, `patchSeamBand`, `patchTracks` | 13 tests establish lane frame, filtered grain, bounded seams and track direction. GPU road remains flat shaded lanes/streaks, with no rut/aggregate normal relief. All patches use global roughness. | **P1:** physically coupled local damp/rut/aggregate masks and normal relief; retain road-coordinate framing. Geometry displacement should be optional and bounded. |
| `rock.js`: `makeRock`, `makeRockField` | 6 tests cover closed topology, determinism, concavity, disposal ownership and GPU pixels. Current source already includes multi-scale fractures and subdued mineral shading from the prior refinement. Some broad faces still read as processed polygons; high detail can be expensive. | **P2:** lower-cost LOD/prototypes, edge/chip hierarchy and geologically coherent fracture sets. Preserve current closed shell, size/burial and owned cleanup; do not restart with a noisy sphere. |
| `sand.js`: `makeSandTerrain` | 5 tests; asymmetric dunes, directional analytic ripples, grains, actual-triangle height sampling and owned cleanup already exist in current source. Dunes are static, fairly coherent parallel ridges. | **Keep core. P2:** optional multi-direction dune fields and sheltered deposition, with meaningful geometry variation. No new sampling repair is needed here. |
| `signage.js`: `loadHelvetiker`, `makeText` | 9 tests; shipped local font, extruded/bevelled geometry, darkening-only colour variation, actual glyph shadows and practical lighting. Labels rendered successfully in the material baseline. Resource lifetime remains caller-managed. | **Keep first phase. P2:** idempotent owned disposal and optional material-safe text variants if integration needs them. A font-loader rewrite or cosmetic recolouring is not justified. |
| `strata.js`: `patchRockStrata`, `patchErosionStreaks` | 13 tests; nonperiodic beds, tilted altitude frame, fall-line erosion and anti-aliasing are implemented. GPU strata/erosion have useful colour structure but largely look painted over the coarse cliff. Erosion globally polishes the surface. | **P1/P2:** local washed-region roughness and bounded relief tied to existing masks. Keep nonperiodic stratigraphy, filtering and fall-line logic. |
| `surface_wear.js`: `patchMicroBreakup`, `patchEdgeWear` | 11 tests including instance positions, physical curvature scale and filtering. Microbreakup is useful. Hard-face edges are not recoverable from smooth curvature derivatives; scalar polishing changes unworn areas. | **P1:** local finish/substrate response and supported curvature/edge inputs. Keep scale-aware anti-aliasing. |
| `terrain.js`: `ground`, `cliff` | 8 tests; real displaced geometry, shared-material isolation and connected cliff ribbon already exist. Sampling mismatch reproduced. Default missing PRNG silently creates a constant lattice despite documentation saying required. Cliff strata changes can be abrupt and its base mesh has no thickness/back. | **P1:** exact triangle sampling and explicit input handling. **P2:** cliff depth/edge closure options and improved multiscale terrain; preserve authored origin. |
| `terrain_shade.js`: `patchTriplanar`, `patchSlopeSplat` | 11 tests; UV-free world projection, slope/altitude gating, patch composition and snow opt-in work. Only albedo changes; grass, scree, rock and snow share the substrate's normal/roughness response. Shared normal-transform defect changes classification under scale. | **P1:** central transform repair and coupled per-zone physical channels. **P2:** optional texture triplets/normal blending instead of only procedural colour. |
| `urban.js`: `patchCurtainWall`, `makePowerLines`, `makeBillboard` | 11 tests. True catenary cables, pixel-width filtering, poster texture and optional practical spill are present. GPU curtain wall looks like a flat grid with synthetic sky colours; this is a distant-facade approximation. Billboards are bright even in the day fixture with `lit:true`; current `ambient` is author-supplied. | **P1 shared normal fix; P2** richer close-facade roughness/reflection variation, lifecycle ownership, viewport updates. Keep catenary and filtered cable rendering. |
| `windows.js`: `patchWindowInteriors`, `makeNightWindows` | 13 tests; room-box parallax, stable room hashes, occupancy, furniture/blinds and pane size gates exist. GPU window room depth is visible. Real glass thickness/refraction is not provided. Cloning shared facade material can remove earlier patches. | **P1 composition contract; otherwise keep. P2** interior atlas/room variety and optional physical foreground glazing. Avoid replacing effective far-view parallax with thousands of interior meshes. |

## New capabilities with a distinct purpose

1. **Fractured ice:** a closed, locally transformed mesh with seeded crack planes, frost/clear-ice masks, physical absorption/transmission and a bounded internal-scattering approximation. It fills a missing material/geometry family. Validate silhouette, topology, background distortion, thickness response and offscreen limitations.
2. **Geometric snow caps and drifts:** complement existing `accumulation.patchSnow`, which is a surface patch, with actual overhang/ledge accumulation and a sampler. Reuse snow masks and shared wind inputs rather than adding a second colour-only snow shader. Test grounding, cap thickness, directional accumulation and disposal.
3. **Projected surface marks:** a reusable decal API for cracks, paint loss, puddle boundaries, markings and localized stains, with metre scale, normal/roughness contribution, controlled depth bias and resource ownership. This supplies authored locality that world-noise patches cannot express. Reuse existing road tracks rather than duplicate them.
4. **Cooling lava/crust:** a bounded animated flow surface with real crust plates or displaced lobes, emissive fissures, temporal crust formation and restrained heat shimmer. Useful after shared material channels are correct; clearly distinguish the visual approximation from fluid/thermal simulation.

For this work window, structured masonry/fabric plus correct local weathering should precede adding four unrelated new modules. Fractured ice is the strongest independent new effect candidate; geometric snow is a compatible second candidate if the accumulation owner agrees.

## Validation plan for the coordinated implementation

- Keep these baseline workspaces fixed and render matched cameras after one library change at a time.
- Add nonuniform object/instance scale equivalence checks to the shared normal code and direct-copy users.
- Compare source height/face samplers against actual triangle raycasts at interior points, including low resolution.
- Exercise roughness/metalness mask response with coverage zero/full and a moving highlight; do not merely inspect generated GLSL text.
- Add true material structure tests: brick course dimensions/mortar, cobble boundaries, weave direction, filtering and tiling.
- Retain all existing topology, determinism, cache, palette, authored-origin and disposal tests unless an explicitly changed contract makes a test obsolete.
- Add final-frame tests for reused compile fixtures where geometry was previously occluded, especially neon.
- Record construction cost, draw calls, triangles, GPU time and retained resources for any new instanced or transmissive effect. Compilation alone is not a quality verdict.

No model calls were made. Implementation ownership and any shared shader/API changes require coordination with the root task before editing modules.

## Coordinated implementation checkpoint: 08:10 UTC

The read-only baseline above remains frozen. The authorized implementation now fixes the following existing contracts:

- Rust, dust and drip deposits use their own coverage for roughness and dielectric metalness. Rust has multiscale corrosion islands and millimetre surface-gradient relief, filtered by pixel footprint. Clean substrate and live strength zero retain the original optical values.
- Micro breakup, edge polish, road ruts, gutters, collected grit, pressed tracks, erosion runs and curtain-wall glass now modify their local roughness masks rather than the entire material. Curtain mullions keep their substrate finish.
- Roadway, neon spill, windows and curtain walls use the shared inverse-transpose normal helper. Other world-surface patches inherit the corrected shared helper.
- Brick has offset courses, mortar and chipped bevel height; cobble has seeded irregular stone cells and recessed sediment; fabric has alternating over/under yarn relief. The structured height and roughness map are linear data, separate from sRGB albedo. All material textures now use linear filtering and mipmaps. These remain UV materials: authors control physical tile scale through UVs/repeat; they are not geometry displacement or a replacement for foreground masonry meshes.
- Texture repeat preserves `bump: 0` and custom bump maps. Tint, weather, ground and night-window cloning preserve registered Astra shader patches and typed independent uniforms while borrowing texture identities. Cache keys track source material version.
- Ground and cliff placement samplers interpolate the actual rendered triangles, including off-vertex positions and transformed parents. Flat cliffs no longer produce nonfinite vertex colors.
- Building factories, roof furniture, text, neon, light trails, power lines, billboards, terrain and cloned night-window materials expose idempotent cleanup of construction-owned resources. Cached materials, borrowed textures, supplied materials and later attachments remain alive. Animated neon/power-line/billboard helpers expose `update` alongside the existing `tick` alias.
- Rock and sand now register their normal relief through the shared normal hook, so patch-preserving clones retain that relief too. Their closed rock topology and exact sand sampler remain intact.

The 15-module test set now contains **196 tests**. The combined run passed 195 and exposed one old normal-spelling assertion in `test_terrain_shade`; updating it to the shared helper made its complete 11-test file pass. All changed Python tests pass Ruff. Tests added here cover off-vertex raycasts, zero-relief finiteness, shared resource ownership, material map registration, weave ordering, filtering, disabled/custom bumps, typed shader cloning and actual GPU optical factors.

The GPU rust probe found 5,388 covered and 9,365 uncovered pixels: every covered pixel became rougher, uncovered roughness stayed at 0.2, and metalness equaled one minus coverage within one 8-bit level. Setting strength to zero restored the base factors within the same tolerance. The complete shader compiled with pre-existing bump and normal maps as well.

Matched 1440 × 810 renders now live under `/tmp/graphics-seven-hour/after/surfaces/`, using the same baseline scene code and cameras. All ten frames across the palette/weathering/terrain, architecture, finish and neon studies rendered on the GPU with zero shader or console errors. These images were inspected. Masonry structure is now visible; larger architectural massing and the legacy generic wood/granite presets still have the limitations described above. The images are diagnostic fixtures, not a claim that a cube palette is a finished environment. A composed ice/shoreline study follows as the approved new capability.


## Existing-family freeze: 09:20 UTC

The coordinated surface pass is frozen for review under `/tmp/graphics-seven-hour/surfaces-final/`, with source hashes and a compact `validation.json`. It reuses the original palette, architecture, finish and visible-neon fixture scenes and cameras. All ten 1440 × 810 frames rendered on the real GPU with zero console or shader errors and were visually inspected. The complete owned test set now passes **208 tests** (199 existing-family tests and nine ice tests), and Ruff passes.

Additional work after the earlier checkpoint:

- `roadway.js` supplies filtered metric normal relief for aggregate, wheel ruts, seam grit, pressed tread and raised track rims. Each fine octave fades by its own wavelength. Repair masks use ordered `smoothstep` edges. The GPU regression measures perturbed normals, exact restoration at live depth zero and preservation of an existing normal map. This is normal relief; silhouettes and collision meshes remain unchanged.
- `terrain_shade.js` filters each procedural octave by pixel footprint. Triplanar mineral shading now has `relief` in metres (default 0.002, zero disables) and `roughness` (default 0.86). Slope splats use independently configurable grass/scree/rock/snow roughness and dielectric metalness. A GPU readback verifies the actual zone values after both patches compose. Shared transformed normals remain the basis for zone classification.
- `materials.js` adds coherent longitudinal wood grain, bent growth rings and seeded knots to weathered and painted wood, with matching linear height and roughness. Grain follows UV +V. Painted relief is smaller than weathered relief; default bump heights are 0.0015 m and 0.006 m. The structured brick/cobble/fabric/wood maps are now 512 square with mipmaps. Contrast, grain scale and streak options affect this path, and explicit external color maps do not acquire unrelated procedural mortar. Tests check weave ordering, grain anisotropy, texture seams, cache sharing, option behavior and custom map preservation.
- `aging.js` separates corrosion islands, centimetre flakes and filtered millimetre grain; the coarse pixel-sized rust relief exposed by a close cylinder study was reduced. The original coverage, clean-substrate optics and strength-zero guarantees remain tested.

Final disposition of the original 15 modules: `aging`, `building`, `materials`, `neon`, `roadway`, `rock`, `sand`, `signage`, `strata`, `surface_wear`, `terrain`, `terrain_shade`, `urban` and `windows` received the fixes above or the earlier ownership/transform/composition fixes. `finish` retains its working lightweight effects and explicitly remains an optical approximation; its matched GPU scene and complete tests passed. None of these effects is declared physically exact. The earlier per-module table retains the remaining limitations, including distant facade approximations, flat patch silhouettes and unmodelled architectural construction detail.

A composed workshop study lives at `examples/graphics_lab/scenes/workshop.js` and `/tmp/graphics-seven-hour/workshop-study/`. Four close and wide GPU views expose real masonry scale, cloth, aligned board grain, worn metal, pavement and road response. Its previous structured-material snapshot remains at `/tmp/graphics-seven-hour/workshop-structure-before/`. The study revealed that a flat cobble material cannot provide foreground stone silhouettes or contact; a separately scoped geometric paving capability is approved next rather than increasing bump amplitude.

## New capability: fractured freshwater ice

`ice.js` exports `makeFracturedIce`. Each seeded cell is a closed, bevelled volume with a nominal local upper surface at zero, variable thickness, optional heave, a physically transmissive material, filtered frost and stress cracks, and true instanced air lenses inside the solid. Authored convex outlines are supported. `userData.sampleHeight(x,z)` queries actual Float32 upper triangles and returns null in fissures or outside; `update` is static, and disposal releases only construction-owned geometry, materials, instances and the optical texture.

The shared fracture deformation runs before gaps are opened. A floe is shrunk toward a point inside its polygon visibility kernel, so neighboring outlines fit and subsequent side-ring cuts remain inward. This corrected an overlap defect that closed-manifold checks alone missed. The regression covers twelve seed/gap combinations down to 2 mm, in addition to closed topology, positive volume, determinism, transformed raycast agreement, internal-air containment, input validation, resource ownership and GPU compilation. An actual checker transmission render verifies thickness-dependent colored absorption and reduced background contrast through frost.

The composed frozen-shoreline study is `examples/graphics_lab/scenes/ice.js`, staged at `/tmp/graphics-seven-hour/ice-study/`, with three fixed 1440 × 810 views. A previous visual checkpoint is preserved at `/tmp/graphics-seven-hour/ice-study-v1/`. Root independently reviewed the updated sheet, submerged objects and shoreline as suitable for integration. The pressure ridge is now visibly tilted and seated against a frost-covered lower floe and the main sheet, using actual transformed vertex contact. All three final GPU views are clean and visually inspected; the immutable source/image snapshot is `/tmp/graphics-seven-hour/ice-final/`.

Limits: optical path uses local top/bottom thickness and an estimated floe width on side faces, not an exact exit intersection. Three transmission sees the opaque scene and does not recursively trace transparent water or another ice volume. The fracture network is a seeded visual model, not a mechanical fracture simulation; very small degenerate chips can be omitted. The exposed pressure slab still has a broadly flat top and geometric fracture bands, and frost distribution could gain finer directional crystal structure in a future polish pass. Large bubbles are individually instanced geometry; at the shoreline study's chosen area/density, the main ice group represents about 174,000 triangles including its air lenses, in two meshes. The complete scenic fixture, including terrain and many unique rocks, is about 805,000 triangles; its observed 130–211 FPS range on the RTX 5090 Laptop GPU is environment-specific, not a portable performance guarantee.

## New capability: geometric paving

`paving.js` exports `makePaving({size, stoneSize, pattern, joint, thickness, relief, seed, color, jointColor, roughness, moisture, name})`. `pattern` selects irregular cobbles or staggered rectangular setts. Units are metres; the nominal walking surface is local y=0, its underside is `-thickness`, and `relief` varies stone height and tilt. The returned named Group has two merged meshes, a static `update`, idempotent construction-owned `dispose`, and `sampleHeight(x,z)` over the exact Float32 upper triangles and recessed joint bed. Queries outside the rectangular patch return null. Requested density is bounded at 4,096 stones by making oversized patches coarser; this is foreground geometry, not a terrain streaming system.

Every stone is a closed solid with trimmed corners, cut bevels, minor chipped edge variation, an intrinsic crown and restrained seeded color variation. The gap is formed by inward clipping of disjoint cells; corners and subsequent rings remain inside their stone footprint. The initial rendered version looked soft and pale, so the accepted version reduced crown/bevel width, retained cut-face normal discontinuities and used a darker mineral base. Fine normal grain fades with pixel footprint. Moisture darkens the albedo and lowers roughness together while metalness remains zero; this is a damp stone finish, not a reflective puddle layer.

Matched evidence is preserved at `/tmp/graphics-seven-hour/workshop-paving-before/` and `/tmp/graphics-seven-hour/paving-final/`. The earlier rounded-stone iteration is `/tmp/graphics-seven-hour/workshop-paving-after-v1/`. The same four 1440 × 810 cameras show the material-only yard before and the real stone yard after. The only accompanying assembly correction was marking authored mounted parts as free placement: the runtime had lowered the wall sign backing by 0.251 m and one interior timber by 0.17 m. The final fixture reports zero automatic placement moves. All four final views were inspected. Dry/wet cobble/setts diagnostic panels are additionally rendered and inspected under `/tmp/graphics-seven-hour/paving-variants/`.

Eight focused tests cover closed positive-volume stones, bounds, deterministic geometry/color, noncrossing footprints, exact raycast sampling through stones and joints under nested transforms, owned cleanup, bounded construction, actual GPU compilation with fog/shadows/normal maps/cloned patches, and GPU optical-channel readback. The moisture probe verifies roughness changing from approximately 0.84 to 0.30, an albedo multiplier near 0.63 and zero metallic response. A particularly useful regression exposed Three's transformed bounding-sphere underestimate under shear: direct local triangle intersection agreed with the sampler, but the world raycast skipped a real stone-edge hit. Paving uses conservative sphere radii to avoid that early cull while retaining exact bounding boxes.

The workshop's 6.9 × 11 m patch contains 1,334 stones and 124,588 triangles in two meshes, using about 3.09 MB of geometry arrays. One measured Node construction took 138 ms; 10,000 height queries took 13 ms. The complete workshop rendered 225–269 FPS on the RTX 5090 Laptop GPU, with 128 draw calls and 147,128 triangles, no shader/console/update errors. These are hardware-specific observations. The final complete owned test run passes **216 tests**: 199 existing-family tests, nine ice tests and eight paving tests. Python test lint passes.

Remaining limits: this is cut-stone paving with a generic procedural mineral finish, not a scan library or a geology simulator. Rectangular patches do not yet conform to arbitrary curved paths or a caller-provided heightfield, and wetness supplies no standing-water optics. The workshop is a composed material study; its simplified building envelope, flat UV brick faces and procedural rust grading remain visible and are not presented as a finished photoreal environment. The original fifteen-module audit and matched fixtures remain frozen independently of this added capability.
