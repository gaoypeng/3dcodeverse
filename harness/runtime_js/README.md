# runtime_js — harness-owned node runtime

Everything Three.js / headless-Chrome the harness does lives here.  **Agent code
never imports from this directory**; it only imports `three` (and
`three/addons/...`), which the harness resolves for it — through the
`--import lib/resolve_three.mjs` hook in node, and through the import map
`serve.cjs::importMapHtml()` in the browser (runtime_js is mounted at
`/__runtime/`; nothing is fetched from a CDN).

## Install

This directory is an ordinary npm package owned by the harness: `package.json`
and `package-lock.json` are committed, `node_modules/` is **not** (gitignored,
~97 MB, 100 packages).  Node itself is a system prerequisite — installing it is
separate from what `npm ci` does here.

**Node floor: 20.6.0** (`package.json` `engines.node`; developed on v24.14.0).
20.6.0 is where `node --import` lands, which `lib/resolve_three.mjs` needs; the
npm dependencies themselves bottom out at node 18, and `node:util.parseArgs`
(the CLI helpers) at 18.3.  Between 20.6 and 22.15 the resolver registers the
older *async* loader hooks (`lib/resolve_three_async.mjs`, a separate loader
thread) instead of the in-thread `module.registerHooks`; the resolutions are
identical.  The python side refuses to spawn an older node with an actionable
message (`codeverse.spatial.node.NODE_MIN`, mirrored by `3dcv doctor`'s `node`
row), and `tests/core/test_portability.py` pins the two declarations together.
The floor is exercised, not assumed: the 93 `node`-marked tests all pass on node
20.19.5 (`CV3D_BINARIES__NODE=/path/to/node20 pytest tests -m "node and not live and not blender"`).

```bash
cd harness/runtime_js
npm ci                                  # exact-lockfile install (~2 s); NOT `npm install`
npx puppeteer browsers install chrome   # only if ~/.cache/puppeteer is empty
```

`bash harness/setup.sh` does both of these (plus the python install and
`3dcodeverse doctor`) and skips `npm ci` when the lockfile has not moved.

Re-run `npm ci` after a fresh clone or after pulling a `package.json` /
`package-lock.json` change — it deletes `node_modules/` first, so never while a
render or bench is running.  Deps: `three` 0.182 (export + render + scene host),
`puppeteer` 24 (headless Chrome, downloads its own build into `~/.cache/puppeteer`),
`three-mesh-bvh` 0.9.14 (accelerated raycasts).  Full guide: `../docs/INSTALL.md`.

```
gpu_launch.cjs        launchBrowser({gpu:'auto'|'on'|'off'}) → {browser, gpu, renderer, shared, release()};
                      rendererInfo(browser).  Callers must release(), never browser.close(): connect-first
                      reuse shares one browser per backend via browser_daemon.cjs (endpoint file under
                      CV3D_CACHE_DIR, connect ~5 ms vs ~0.55 s launch; CV3D_BROWSER_REUSE=off disables).
                      WSL2 hardware WebGL (ANGLE gl-egl + Mesa d3d12 env), UNMASKED_RENDERER probe,
                      negative verdict cached 20 min in ~/.cache/codeverse/gpu_probe.json, SwiftShader fallback
browser_daemon.cjs    detached keeper of the shared browser (one per gpu|cpu backend): advertises its
                      ws endpoint, reaps after ~90 s idle (endpoint-file mtime heartbeat + open-page count)
serve.cjs             serveDirs({root, mounts, routes}) loopback static server (MIME table, CORS);
                      runtime_js is always mounted at /__runtime/; importMapHtml() maps 'three' +
                      'three/addons/' onto it — nothing is ever fetched from the internet
export_glb.mjs        node --import lib/resolve_three.mjs export_glb.mjs --ws <ws> [--entry src/object.js]
                      [--out artifacts/object.glb] [--census artifacts/census.json]
                      [--normalise 0|1] imports the agent module, build(THREE) (awaits promises), validates
                      (NaN/empty-bbox errors name the mesh + part), runs an exported selfcheck(THREE, root)
                      if any (throw → SelfCheckError), bakes InstancedMesh copies into named meshes
                      (lib/instances.mjs — trimesh ignores EXT_mesh_gpu_instancing), keeps the source
                      placement (off-ground/off-centre → warning + census.placement_offset; --normalise 1
                      translates instead, for dataset canonicalisation only), strips textures (warning),
                      writes GLB + census.json; on failure export_error.json +
                      {ok:false,error:{type,message,file,line,frames,part?}}
render_glb.mjs        --glb --out --views '[{name,azimuth,elevation}]' [--mode shaded|wire|normals|silhouette|clay]
                      [--width --height] [--isolate A,B] [--explode 0.3] [--background studio|white|transparent]
                      [--anim-time t] [--gpu auto|on|off] [--shadow 0|1] [--fill 0.85]
                      writes view_<name>.png + views.json; last stdout line = JSON record.  Thin entry:
                      args/JSON protocol from lib/cli.mjs, browser + server from lib/host_env.mjs,
                      browser release from lib/host_page.mjs, everything visual from lib/browser/
lib/resolve_three.mjs node --import hook: bare 'three' / 'three/addons/*' (and three-mesh-bvh) resolve
                      from runtime_js/node_modules for modules anywhere on disk (NODE_PATH is CJS-only);
                      module.registerHooks on node >= 22.15, module.register + lib/resolve_three_async.mjs
                      on the 20.6 floor.  Redirect rule stated once in lib/three_redirect.mjs
lib/node_polyfills.mjs FileReader/Blob/self shims so GLTFExporter writes binary GLB without a DOM
lib/census.mjs        per-part tri counts, world bboxes, materials, NaN check naming mesh + part (node + browser)
lib/instances.mjs     bakeInstancedMeshes(THREE, root): InstancedMesh → Group of named plain meshes
lib/stack.mjs         Error → {type,message,file,line,frames} with workspace-relative src/ paths
lib/syntax_check.mjs  `node --input-type=module --check` per file to locate ESM SyntaxErrors
lib/browser/*.js      page-side ESM (served through /__runtime/): renderer.js (THE WebGLRenderer factory —
                      sRGB + ACES + PCF shadows + pixel ratio 1, used by the object rig AND the scene host),
                      camera_fit.js (azimuth/elevation → tight bbox fit; the distance math itself is
                      lib/orbit.mjs::fitDistance, shared with the scene orbit rig), studio.js
                      (RoomEnvironment PMREM + key/fill/rim, shadow catcher, render modes; re-exports
                      renderer.js), render_rig.js (load GLB, isolate/explode/anim, views)

probe_scene.mjs       scene build gate: boot src/scene.js, census, update(t,dt), first-camera checks;
                      --compile folds the full shader preflight into the SAME boot (shader_report);
                      --sun-azimuth returns harness-fitted overview + per-group camera specs (lib/orbit.mjs)
check_shaders.mjs     standalone shader preflight (static GLSL audits + GPU compile, file:line mapped)
render_scene.mjs      authored cameras + orbit rig renders at times, metrics.json/views.json
lib/host_env.mjs      ESM adapters over gpu_launch.cjs + serve.cjs (launchBrowser, serveWorkspace)
lib/host_page.mjs     node-side page driver: serve ws, launch, boot scene, collect errors, releaseBrowser
lib/scene_host.mjs    page-side host (window.__c3v): boot/renderAt/census/compileAll/fps/cameraChecks
lib/orbit.mjs         THE camera-fit owner: fitDistance (exact per-corner frustum fit — also used by the
                      object rig through lib/browser/camera_fit.js), fitOverviewCamera, fitZoneCamera
                      (eye level), fitOrbitCameras, framingBox
lib/backdrop.mjs      THE sky/ground/content classifier (name + world-box rules), shared by the census
                      (host_census.mjs) and the frame-coverage instrument (host_coverage.mjs)
lib/host_metrics.mjs  page-side frame instruments: sampleFrame (the shared 96x54 readback grid),
                      frameStats (luminance), nearGeometry (camera-in-geometry rays)
lib/shader_report.mjs shared shader-preflight report builder (static + compile stages)
lib/glsl_audit.mjs    static GLSL audits + compiler-line → file:line mapping
```

Conventions (from `codeverse/conventions.py`): Y up, +Z front, meters; azimuth 0 = front,
counter-clockwise seen from above (90 = camera on +X); elevation above the horizon.  Views are
fitted per camera so the projected bbox fills ~85 % of the frame.

Python entry points: `codeverse.spatial.node.run_node`, `codeverse.spatial.render.render_glb`,
`codeverse.languages.threejs.ThreeJsRuntime`.

Quick checks:
```bash
node --import lib/resolve_three.mjs export_glb.mjs --ws /path/to/ws
node render_glb.mjs --glb ws/artifacts/object.glb --out /tmp/r --views '[{"name":"front","azimuth":0,"elevation":10}]'
```
