# Graphics Lab

See the [current maintenance and expansion record](REVIEW_2026-09-23.md),
[earlier refinement](REFINEMENT.md), and
[initial validation and actual model trials](VALIDATION.md) for evidence and limits.

Sixteen executable studies of fire, water, terrain, vegetation,
atmosphere, worked materials and independent shader files, plus three GPT-6 Luna cases.
All images and films come from Three.js scene code;
there are no prerendered backgrounds or generated image assets.

From the `harness/` directory:

```bash
python examples/graphics_lab/build.py --out /tmp/graphics-lab-v2 --render --video
python examples/graphics_lab/verify_media.py --out /tmp/graphics-lab-v2
node examples/graphics_lab/check.mjs --out /tmp/graphics-lab-v2
node examples/graphics_lab/serve.mjs --out /tmp/graphics-lab-v2
```

Open the loopback URL printed by the server. The gallery offers H.264 films,
full-resolution stills, and live orbit controls with authored camera presets,
pause and time scrubbing. Everything, including Three.js, is served locally.
Opening `index.html` as a `file:` URL does not load browser ES modules; use the
server. The default output is `examples/graphics_lab/output/` (git-ignored).

`--case fire` selects one case to render again; the gallery still includes all
nineteen entries. Repeat `--case` to select several cases. `--width 1920 --height 1080`
increases capture resolution. `--seconds 6
--fps 24` controls the film. Video export requires ffmpeg, either on PATH or
provided by an installed `imageio-ffmpeg` package. No installation happens
automatically. Stills and live exploration need only the harness runtime.

Each scene lives in `scenes/` and implements the normal
`createScene({THREE, renderer, loaders}) -> {scene, cameras, update}` contract.
The builder stages self-contained workspaces in `<out>/cases/`, using a byte
snapshot of the current shipped `src/lib` modules and all registered scenes.
Independent `.vert`, `.frag` and `.glsl` files under `shaders/` are copied into
every workspace and the published `src/shaders/`, with a separate shader digest
map in the manifest. Their relative paths are preserved. Shader byte changes
receive the same stale-media protection as scene and library changes.
It checks that every scene exists before writing anything. Stills use `runtime_js/render_scene.mjs`;
films use the same scene host, deterministic absolute simulation times and
the same ACES/GTAO/bloom/grade chain. `renders/<case>/metrics.json` and
`<case>.capture.json` record actual execution and renderer information.
`verify_media.py` checks every published/staged source hash, primary scene identity,
encoded-file digest and capture record. It decodes every frame, verifies frame
counts/rates, and compares retained encoding inputs at three times with the
decoded film, including motion where the input frames measurably change. The
first frame also matches its authored-camera still. `capture-plan.json`, when
present, specifies the complete required film/scene/camera set including extras.
Reports and motion contact sheets go in `media-verification/`; pixel change is
not a realism score. Historical captures without input frames/digests require
`--allow-legacy` and are explicitly identified as weaker evidence.

Use a fresh `--out` directory whenever scene or shared library sources change.
Once an output contains media, staging verifies its existing manifest, published
source files, staged workspaces and available capture-time hashes before writing.
A mismatch is refused, including changes to an unselected scene: the builder
cannot attach today's code to yesterday's film. An unchanged-source incremental
run remains supported, for example:

```bash
python examples/graphics_lab/build.py --out /tmp/graphics-lab-v2 --case fire --render --video
```

Staging without `--render` or `--video` creates source workspaces only. A directory
without media may be refreshed freely. Older capture records without source
hashes can only be checked against their retained source manifest; they do not
gain retrospective capture provenance. These checks cover scene/library bytes,
not runtime host revisions. Keep the runtime revision with an archival release.
Existing packages and controlled-comparison snapshots are left untouched.

The manual order is fire, ocean, stream, sand, rocks, meadow, tree, cloud volume,
smoke, ice, waterfall, rain shelter, workshop, campfire/bonfire, structural fire and
external GLSL material. Agent entries A1-A3 are candle
courtyard, coastal creek and garden workbench. The first six retain their original
order and remain the source-and-video package selection. `overview.jpg` uses
three columns and as many rows as the available manual previews require; missing
previews and agent entries are omitted from that contact sheet.

The scenes demonstrate realtime approximations, not a combustion, fluid or
granular physics solver. Ocean reflections use a mean plane; stream refraction
samples the opaque scene and cannot see transparent or offscreen objects; overlapping
transparent fire/glass volumes need careful placement. Sand dunes are static
terrain with animated windborne grains. See each module's JSDoc for limits.

## Library entry points

| Module | Factory | Principal options |
| --- | --- | --- |
| `fire.js` | `makeFire`, `makeCandle` | radius, height, seed, quality; candle wax, wick and drips |
| `firefield.js` | `makeFireField` | emitters, wind, smoke, embers, lighting, occluders, depthResolution, quality |
| `ocean.js` | `makeOceanSurface` | width/depth, waveHeight, wavelength, windDirection, shoreline |
| `stream.js` | `makeStream` | descending path points, width/depth, speed, obstacles, attenuationDistance, reflectionSize, caustics |
| `sand.js` | `makeSandTerrain` | size, duneHeight/duneSpacing, windDirection, rippleSpacing |
| `rock.js` | `makeRock`, `makeRockField` | type, size, seed, moisture, weathering |
| `meadow.js` | `makeMeadow` | size, density, height, heightAt, mask, diversity, seedHeads, wind, shadows |
| `tree.js` | `makeTree`, `makeShrub` | species, height, crownRadius, leafDensity, maxLeaves, leafSegments, autumn, wind |
| `cloudvolume.js` | `makeCloudVolume` | size, coverage, density, wind, sunDirection, quality, seed |
| `smoke.js` | `makeSmoke`, `makeSteam` | height, radius, spread, riseSpeed, wind, density, quality, seed |
| `ice.js` | `makeFracturedIce` | size, thickness, crackDensity, gap, frost, bubbles, heave, seed |
| `waterfall.js` | `makeWaterfall` | width, height, speed, thickness, breakup, foam, spray, quality |
| `paving.js` | `makePaving` | size, stoneSize, pattern, joint, thickness, relief, moisture, seed |

The listed factories return Three.js objects with `userData.update(t, dt)` and
`userData.dispose()`. Coordinates and dimensions are local metres; move the
returned object as a whole. Drive absolute seconds once per frame. Rock is
static; its update hook is intentionally a no-op. Terrain and water sampling
methods return local positions/heights, as documented in the source.

For an existing terrain, pass `ground: false` to `makeMeadow`, and give it the
same `heightAt` function that built the terrain. Grass shadows need a tight
sun frustum and sufficient resolution for centimetre-wide leaves. The runtime
uses `PCFShadowMap`, the filtered shadow type supported by the installed Three.

Background reading for the wind architecture:
[GPU Gems, Rendering Countless Blades of Waving Grass](https://developer.nvidia.com/gpugems/gpugems/part-i-natural-effects/chapter-7-rendering-countless-blades-waving-grass).
The implemented meadow uses actual curved leaf geometry, per-leaf phases and
shared travelling gusts; it does not copy the chapter's texture-card geometry.

Stream optics use [Three's physical transmission](https://threejs.org/docs/pages/MeshPhysicalMaterial.html),
with IOR 1.333 and depth-dependent Beer attenuation. `waterColor` describes the
absorption colour over `attenuationDistance` metres (default 3). Optional
`reflectionSize: 256..2048` adds bank reflections; default 0 uses the environment
map only. Use at most one planar reflector in a scene. The brook study enables
1024-pixel bank reflections. Bed caustics are a bounded ripple-curvature
approximation applied to direct lighting, not a fluid or photon simulation.

## Independent shader files

The external shader study loads [a vertex shader](shaders/external_shader.vert),
[a fragment shader](shaders/external_shader.frag) and
[shared GLSL helpers](shaders/external_shader_common.glsl) from async
[scene creation](scenes/external_shader.js). It uses `THREE.FileLoader`,
module-relative URLs and `ShaderMaterial` with `THREE.GLSL3`; no bundler or raw
shader import extension is required. JavaScript owns the geometry, uniforms and
absolute-time update. This is an artistic alloy response with analytic studio
reflections, not measured anodization or scene-wide ray tracing.

For complete API conventions see Three's [ShaderMaterial documentation](https://threejs.org/docs/pages/ShaderMaterial.html)
and [FileLoader documentation](https://threejs.org/docs/pages/FileLoader.html).
Native OpenGL host programs require adaptation to WebGL and GLSL ES.

## Controlled refinement comparison

The retained `output/refinement-baseline/<case>/src` workspaces preserve the first
revision. Historical comparison images and their snapshots remain independent
of the expanded gallery. To produce a new comparison, use a separate output and
a baseline whose shared dependencies support the module revision being compared:

```bash
python examples/graphics_lab/compare.py --baseline /path/to/compatible-baseline --out /tmp/graphics-lab-comparison
python examples/graphics_lab/build.py --out /tmp/graphics-lab-comparison
```

`<out>/comparison.html` provides before/after sliders for ocean, stream, rock and
meadow. Each pair changes exactly one library module; cameras, lighting, scene
source, simulation time and other libraries are byte-identical. Source hashes
and render metrics are linked from the page. The main gallery additionally
contains revised scene dressing. The ignored baseline/output artifacts must be
retained to reproduce this historical comparison on another machine. The browser
checker reads the comparison manifest and verifies its actual image pairs and
slider count; it does not assume a fixed number of comparisons.

## Transfer a source-and-video snapshot

After rendering the gallery, package its first six library studies:

```bash
python examples/graphics_lab/package.py --out /tmp/graphics-lab-v2 --name my_six_studies
```

The script creates an offline video page, six scene modules, their shared
library and external shaders, videos, previews, capture metadata and a verified ZIP
in `<out>/packages`. When available, it checks encoded movie digests and bundles
the retained input PNGs at their original package-relative paths.
It validates published and capture-time source hashes, checks archive integrity
and refuses existing package names. The runtime and npm dependencies are not
bundled. See [REFINEMENT.md](REFINEMENT.md) for retained revision packages.

## Validation

The module-specific tests in `tests/scene_runtime/lib/`
cover relevant determinism, topology, sampling, transforms, lifecycle, actual
GPU compilation, and selected final-render animation checks. The shadow-filter
regression test inspects the linked GPU shader so a deprecated enum cannot
silently revert filtering to basic shadows again. Gallery staging regressions in
`tests/graphics/test_graphics_lab_build.py` cover stale-media refusal, unchanged
incremental builds and missing-scene preflight. Visual review of rendered
frames remains necessary; a green compile test is not an aesthetic verdict.

`check.mjs` opens the staged gallery in the real GPU browser, decodes each
available film, opens every live scene, switches every authored camera, checks
pause and time scrubbing, reopens the live tab, and cancels a pending boot.
It writes `browser-check.json`, `gallery.png` and each `<case>.live.png`.
Run it after building the output. All example UI, code and documentation must
remain in English.

Actual model-generated case runs are stored separately in `output/agent_runs/`.
Their source, prompts, telemetry and renders distinguish agent-produced scenes
from these hand-composed library studies.
The three retained Luna modules live in `agent_cases/`; their provenance and
review history are documented there. Gallery entries A1-A3 identify them as
model cases. All sixteen numbered scenes are manually developed studies.
