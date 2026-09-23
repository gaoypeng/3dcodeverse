# Graphics effects development record — September 23, 2026

## Objective and scope

The user requested a thorough review and refinement of the existing graphics
effects, plus useful new effects, allowing seven hours of sustained development.
Work began around 07:27 UTC. This record separates completed verification
checkpoints from work still awaiting final integration.

The review covers all 50 existing Three.js library modules, their agent-facing
catalog, lifecycle and composition contracts, rendered appearance and runtime
behavior. It also checks the separate GLSL graphics cookbook/recipe surface and
OpenGL authoring guidance. Existing upload packages remain immutable; new
gallery revisions and transfer packages use distinct artifacts.

Every existing module needs an explicit disposition: improve with evidence,
retain with justification, or supersede while preserving a documented caller
path. Passing compilation alone does not establish visual quality. New effects
must fill a meaningful gap, remain usable by scene authors, and have inspected
GPU cases plus appropriate behavioral validation.

## Review ownership

| Family | Existing modules | Evidence record |
| --- | --- | --- |
| Weather, water and atmospheric light | accumulation, atmosphere, caustics, celestial, clouds, damp, godrays, rain, sky, submerged, veils, water, watermist, waterside, wetground | `graphics_audit_weather.md` |
| Living scenes and animation | canopy, cloth, dapple, figure, fire, flock, flowers, foliage_shade, grass, meadow, smalllife, woodland | `graphics_audit_life.md` |
| Built environment and surfaces | aging, building, finish, materials, neon, roadway, rock, sand, signage, strata, surface_wear, terrain, terrain_shade, urban, windows | `graphics_audit_surfaces.md` |
| Shared code and recent water | environment, instancing, merge, noise, ocean, place, shader, stream | `graphics_audit_core.md` |

The complete baseline source, original hashes and export inventory are retained
in `examples/graphics_lab/output/seven-hour/`. The inventory contains 50 rows;
reviews must not silently omit older effects because recent showcase modules
already have stronger tests.

## Work sequence

1. Inspect each module and its tests; render representative factory/material
   combinations and identify visual, numerical and ownership defects.
2. Correct substantive existing defects and improve effect appearance, with
   fixed-camera evidence where appearance changes. Keep shared rendering
   behavior stable unless a separately demonstrated renderer defect requires it.
3. Add distinct missing capabilities identified by that review. Use bounded
   algorithms, absolute simulation time, seeded construction, documented local
   coordinates and owned-resource disposal. Do not duplicate an existing effect
   under a new name merely to increase the library count.
4. Integrate agent guidance, inspect composition cases and exercise the library
   through independent coding-agent cases where useful. Preserve source/model
   provenance and distinguish authored demonstrations from model output.
5. Run relevant regression checks, real GPU rendering, animation and browser
   controls; inspect code/video correspondence and English-only example text.
   Record limitations and performance costs, then audit the full objective.

## Maintenance checkpoint

All 50 original modules have an explicit, evidence-backed disposition: 47 received
visual, behavioral or ownership corrections; three were retained with stated
justification. The original source/hash baseline remains unchanged. Six new visual
modules (tree, cloud volume, ice, smoke, waterfall and paving) and one resource
ownership module have inspected cases and focused tests.

The complete offline suite passed **2,890 tests** in 196.94 seconds, with one
unrelated Pillow deprecation warning. Ruff also passed. Retained logs are
`/tmp/graphics-seven-hour/offline-maintenance-checkpoint.log` and
`/tmp/graphics-seven-hour/maintenance-ruff.log`. These establish the maintenance
checkpoint before the later fire-field and external-shader extension.

Substantive final maintenance corrections include:

- Stream, calm water and wet-floor captures use the correct transformed plane and
  restore nested renderer state after success or exceptions. Sixteen real GPU
  state cases pass; transformed mirror-camera error falls from 6.876 metres to
  floating-point noise. Existing raw water/floor pixels remain bit-identical.
- Cloud support no longer reaches the proxy box walls or wraps separated shapes
  through the boundary. Five seeds and multiple times/cameras were inspected.
  Wind moves internal density detail; whole-cloud translation remains explicit.
- Tree leaf geometry can be reduced independently of seeded leaf placement and
  woody structure. The rain composition falls from 20.7 to 12.4 million triangles
  while retaining its foliage count and silhouette at the tested cameras.
- Rain shelter coverage, stream/meadow finite HDR edges, true terrain sampling,
  owned resource disposal, transformed normals and animation contact retain their
  focused actual-GPU/behavioral evidence in the family records.
- The revised Luna creek now matches bank geometry to its water grade and seats
  actual pebbles. Original CLI output and later delegated refinement remain
  separately identified in the agent-case provenance.

The three GLSL recipe films have been decoded and compared with ordered source
frames. The gallery verifier now binds source snapshots, encoded-file digests and
three retained input PNGs; it checks complete camera plans and rejects substituted
primary scenes, stale sources, changed video bytes and frozen animation. Pixel
change is not an aesthetic score.

## User-requested extension after maintenance

The subsequent request specifically adds campfire, large open fire and burning-house
visual cases, and asks whether Three.js can consume independent GLSL files. A real
GPU audit found that scaling the original fire by ten preserves almost the same
image structure and motion; it also integrates density behind embedded opaque
walls. The new bounded shared multi-emitter field now provides optional explicit
opaque-depth termination. Ten focused tests and an independent depth/layer/opacity/
displacement review pass. Campfire, open-bonfire and structural-fire compositions
have inspected GPU frames. Existing candle and small-fire behavior is retained.

Independent `.vert`, `.frag` and `.glsl` loading already works in the scene runtime,
writer, snapshots and candidate promotion. Guidance now documents asynchronous
local FileLoader loading; standalone asset builders remain synchronous. Shader
preflight now maps external compile errors to their original file and line.
Real GPU valid and broken external shaders have source-line tests. Gallery
staging, capture, verification and transfer packaging preserve those shader files
and retained input frames; 22 build/media/package regressions pass. The completed
external-shader material study has nine inspected stills, finite HDR/replay/disposal
checks, a source-bound six-second film and a passing browser-control check.

## Final integration

The first combined attempt recorded 2,917 passes and two failures: Chrome closed
during a cloud probe, and the mixed external/inline GLSL support downgraded a static
diagnostic from an unused shader. The isolated cloud module passes all seven tests;
the diagnostic was repaired using unambiguous evaluated-source participation.
Twenty-nine focused diagnostic tests pass. The interrupted `gallery-v1` capture
remains a failed attempt, separate from the final gallery.

The complete final offline suite passed **2,922 tests in 225.65 seconds**, with one
existing Pillow deprecation warning. Ruff passed. Logs are
`/tmp/graphics-seven-hour/offline-final-clean.log` and `final-ruff-clean.log`.
Regression and final video capture use separate task-specific browser caches and
run sequentially. The frozen `gallery-v2` registry contains 19 cases, 58 library
modules and three independent shader files. All 159 stills across 53 cameras
rendered cleanly. The complete 30-film / 4,320-frame plan passed strict decode,
source ownership, encoded digest and retained input-frame checks. Browser checks
passed all 19 cases and 53 cameras with playback, pause/scrub/reopen/cancellation
and zero errors. Twenty-seven films demonstrated anchor motion; static or very
subtle rock/sand/workshop views are not reported as motion evidence.

The new first-six transfer package contains 113 files and 74,973,979 bytes, with
source/capture checks, ZIP CRC and extracted-file hashes verified. Original ZIPs,
comparison baselines and published historical media remain byte-identical.
The [final gallery record](../examples/graphics_lab/REVIEW_2026-09-23.md) links the
actual output, package, verification reports, costs and remaining visual limits.
Passing checks does not imply fluid/combustion simulation, recursive transparent
transport or photorealism. Far terrain, some geology/cloud shapes, fire highlights
and the authored architecture retain visible procedural limits.
