# Graphics Lab

Seventeen hand-composed studies of fire, water, terrain, vegetation, atmosphere,
worked materials and independent shader files, covering the effect modules in
`codeverse3d/languages/scene_threejs/starter/src/lib/`.  Every image and film
comes from Three.js scene code rendered through the production scene host; there
are no prerendered backgrounds or generated image assets.  It is a development
lab: no run path uses it.

From the `harness/` directory:

```bash
python examples/graphics_lab/build.py --out /tmp/graphics-lab --render --video
node examples/graphics_lab/serve.mjs --out /tmp/graphics-lab
```

Open the loopback URL printed by the server.  The gallery offers H.264 films,
full-resolution stills, and live orbit controls with authored camera presets,
pause and time scrubbing.  Everything, including Three.js, is served locally;
opening `index.html` as a `file:` URL does not load browser ES modules.  The
default output is `examples/graphics_lab/output/` (git-ignored).

`--case fire` renders one case again (repeat it to select several); the gallery
still lists every case.  `--width 1920 --height 1080` raises capture resolution;
`--seconds 6 --fps 24` controls the film.  Video export needs ffmpeg on PATH or an
installed `imageio-ffmpeg`.  Without `--render`/`--video` the build only stages
source workspaces.

Each scene in `scenes/` implements the normal
`createScene({THREE, renderer, loaders}) -> {scene, cameras, update}` contract.
The builder stages one self-contained workspace per case in `<out>/cases/`, with a
copy of the current `src/lib` modules and the `.vert`/`.frag`/`.glsl` files under
`shaders/` (relative paths kept).  Stills use `runtime_js/render_scene.mjs`; films
(`capture.mjs`) use the same scene host, deterministic absolute simulation times
and the same ACES/GTAO/bloom/grade chain.  `renders/<case>/metrics.json` and
`<case>.capture.json` record the renderer and timing.  `overview.jpg` is a
three-column contact sheet of the available stills.

The scenes demonstrate realtime approximations, not combustion, fluid or granular
solvers; each module's JSDoc states its limits, and
`codeverse3d/prompts/scene_threejs/effects_catalog.md` is the factory table.

## Independent shader files

The external shader study loads [a vertex shader](shaders/external_shader.vert),
[a fragment shader](shaders/external_shader.frag) and
[shared GLSL helpers](shaders/external_shader_common.glsl) from async
[scene creation](scenes/external_shader.js), with `THREE.FileLoader`,
module-relative URLs and `ShaderMaterial` + `THREE.GLSL3` — the loading pattern the
scene contract (`prompts/scene_threejs/contract.md`) teaches.

## GLSL recipe studies

`build_recipes.py [--video] [--case rain_window]` renders the three fragment
shaders in `glsl_cases/` through the production GL host (`GlHost`), seeding each
with the cookbook recipes it calls exactly as the `graphics` track does.  Output
goes to `output/recipes/` with an `index.html` of stills or 144-frame films.

All example UI, code and documentation stay in English.

## Library regression checks

Run `python -m pytest tests/scene_runtime/lib tests/graphics -q -n2` from
`harness/` when maintaining effects. Besides shader compilation, these checks
measure transformed wind and cloth normals, light selection, underwater
transport, material filtering, and feedback initialization on the real rendering
paths. Use the rendered studies above to assess appearance and motion as well;
passing numerical checks alone does not establish visual realism.

The moonlit coast (`--case night`) exercises the connected cloud deck, restrained
star field, physical moon size, and their reflections. `makeClouds` now integrates
one 3D density field; `quality: 'low' | 'balanced' | 'high'` controls sampling cost.
The original alpha-map helper `cloudTexture` remains available for custom sprites.
