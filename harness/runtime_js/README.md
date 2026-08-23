# runtime_js — harness-owned node runtime

Everything Three.js / headless-Chrome the harness does lives here.  **Agent code
never imports from this directory**; it only imports `three` (and
`three/addons/...`), which the harness resolves for it.

```
gpu_launch.cjs        launchBrowser({gpu:'auto'|'on'|'off'}) → {browser, gpu, renderer}; rendererInfo(browser)
                      WSL2 hardware WebGL (ANGLE gl-egl + Mesa d3d12 env), UNMASKED_RENDERER probe,
                      negative verdict cached 20 min in ~/.cache/codeverse/gpu_probe.json, SwiftShader fallback
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
                      writes view_<name>.png + views.json; last stdout line = JSON record
lib/resolve_three.mjs node --import hook: bare 'three' / 'three/addons/*' (and three-mesh-bvh) resolve
                      from runtime_js/node_modules for modules anywhere on disk (NODE_PATH is CJS-only)
lib/node_polyfills.mjs FileReader/Blob/self shims so GLTFExporter writes binary GLB without a DOM
lib/census.mjs        per-part tri counts, world bboxes, materials, NaN check naming mesh + part (node + browser)
lib/instances.mjs     bakeInstancedMeshes(THREE, root): InstancedMesh → Group of named plain meshes
lib/stack.mjs         Error → {type,message,file,line,frames} with workspace-relative src/ paths
lib/syntax_check.mjs  `node --input-type=module --check` per file to locate ESM SyntaxErrors
lib/browser/*.js      page-side ESM (served through /__runtime/): camera_fit.js (azimuth/elevation →
                      tight bbox fit), studio.js (RoomEnvironment PMREM + key/fill/rim, ACES, sRGB,
                      shadow catcher, render modes), render_rig.js (load GLB, isolate/explode/anim, views)
```

Conventions (from `codeverse/conventions.py`): Y up, +Z front, meters; azimuth 0 = front,
counter-clockwise seen from above (90 = camera on +X); elevation above the horizon.  Views are
fitted per camera so the projected bbox fills ~85 % of the frame.

Python entry points: `codeverse.spatial.node.run_node`, `codeverse.spatial.render.render_glb`,
`codeverse.languages.threejs.ThreeJsRuntime`.

Quick checks:
```
node --import lib/resolve_three.mjs export_glb.mjs --ws /path/to/ws
node render_glb.mjs --glb ws/artifacts/object.glb --out /tmp/r --views '[{"name":"front","azimuth":0,"elevation":10}]'
```
