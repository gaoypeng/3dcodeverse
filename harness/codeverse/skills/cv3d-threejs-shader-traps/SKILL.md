---
name: cv3d-threejs-shader-traps
description: "Use when a threejs or scene_threejs session writes a ShaderMaterial or an onBeforeCompile patch, or when shader_preflight reported an error. Write custom GLSL that survives this harness's shader gate on the first build. Says which mistakes the harness detects statically, which it detects at runtime, which it cannot see at all, and corrects three widely repeated three.js warnings that do not apply to this renderer."
license: Apache-2.0
compatibility: three@0.182.0, headless WebGL via puppeteer. Rules read from runtime_js/lib/glsl_audit.mjs, runtime_js/lib/host_compile.mjs, runtime_js/lib/shader_report.mjs and runtime_js/lib/browser/renderer.js.
metadata:
  evidence: mixed
  evidence_note: "Every rule is read from the live runtime_js source and cross-checked against the shader_report merge path. Incidence is NOT measured: bench/out holds 4 scene_threejs runs, and all 4 shader_preflight reports are clean INFO (10, 9, 14 and 11 programs compiled; 1-12 custom materials). The corpus therefore supports the detection map, not a defect rate."
  verified: "2026-08-25"
  owns: "shader/compile_or_binding"
  target_metric: "shader_preflight_findings"
  target_direction: "down"
  target_unit: "WARN+ERROR findings per run"
  target_measurable: "true"
  target_baseline: "0.00 findings per run; n=3 (bench/out, 2026-08-25) - every recorded scene run is clean, so there is no headroom to improve, only a regression to catch"
---

# three.js shader traps that this harness actually detects

**When:** your scene builds a `THREE.ShaderMaterial` / `RawShaderMaterial`, or patches a built-in
material through `onBeforeCompile`, or `shader_preflight` said something.

The GLSL recipes are in `codeverse/prompts/scene_threejs/glsl_cookbook.md` — start from its
`makeShaderMaterial` and its 16 pitfalls. What follows is the part the cookbook does not say:
**which of those pitfalls a gate will catch, at what severity, and which ones nothing catches.**

## The detection map

Everything below lands in the single `shader_preflight` gate report, merged by
`runtime_js/lib/shader_report.mjs`.

**Static pass** — `glsl_audit.mjs`, over the GLSL string literals in your source, no GPU needed:

| kind | severity | fires when |
|---|---|---|
| `include_not_alone` | ERROR | an `#include` shares its line with anything else |
| `version_directive` | ERROR | a `#version` line in a `ShaderMaterial` (three prepends its own); WARN in a `RawShaderMaterial` if it is not first |
| `fragment_out_and_gl_fragcolor` | ERROR | the fragment declares `out vec4` **and** writes `gl_FragColor` |
| `glsl3_gl_fragcolor` | ERROR | `glslVersion: THREE.GLSL3` plus `gl_FragColor` |
| `undeclared_uniform` | ERROR | GLSL reads `uTime` but no string declares `uniform float uTime;` |
| `unbound_uniform` | ERROR | GLSL uses `uTime` but no JS outside a template literal writes `uTime:` or `uTime =` |
| `precision_directive` | WARN | your own `precision ... float;` in a `ShaderMaterial` |
| `chunk_dropped` | WARN | an `onBeforeCompile` `.replace()` of `#include` that does not keep the include |
| `no_fog` | WARN | the scene uses fog and a full ShaderMaterial fragment has no fog chunk |

**Runtime pass** — `host_compile.mjs::materialAudit`, walking the booted scene graph:

* `unbound_uniform` **ERROR** — the material's fragment or vertex source contains `uTime` but
  `material.uniforms.uTime` is missing.
* `no_fog` **WARN** — `scene.fog` is set and this `ShaderMaterial` neither has
  `material.fog && material.uniforms.fogColor` nor mentions `fog_fragment` / `fogColor`.
  Exempt: materials whose object or material name matches `sky|dome|stars|cloud|sun|moon`, or
  that are `side: THREE.BackSide` with `depthWrite: false`.

Plus GPU compile: every program is force-compiled, and a compiler error is mapped back to
`file:line` in *your* source. A scene that will not boot produces one ERROR of kind `boot`.

## Name the time uniform exactly `uTime`

Both audits are hard-coded to the identifier `uTime` (`/\buTime\b/`). Call it `time`, `u_time`
or `iTime` and you get **no** protection from either check — the shader compiles, the uniform
never updates, and the scene is silently frozen. `animation_life` is **0.431, the lowest
criterion in the whole corpus** (8 rounds over 4 scene runs), and the judge's words for it are
*"No animation is visible; the scene is completely static"* and *"MONTAGE 2/3 (t=0s) and
MONTAGE 3/3 (t=1.5s) shows identical frames"*. Naming the uniform `uTime` is the cheapest
insurance in this file.

The rule has two halves and they are checked separately:

```js
// GLSL side: a JS uniforms entry is NOT a GLSL declaration.
const fs = `uniform float uTime;      // <- or 'undeclared_uniform', ERROR
            void main() { gl_FragColor = vec4(vec3(sin(uTime)), 1.0); }`;
// JS side: bind it, and mutate .value - never replace the uniforms object.
const mat = new THREE.ShaderMaterial({ uniforms: { uTime: { value: 0 } }, fragmentShader: fs });
mat.userData.update = (t) => { mat.uniforms.uTime.value = t; };   // driven from update(t, dt)
```

## Fog is real here, and doubly detected

`scene.fog` reaches a material only through three's own chunks. A custom shader without them
keeps full contrast while everything around it recedes — the classic "cardboard cut-out in the
haze". Both the static pass and the runtime pass flag it, so it costs you two warnings.

Three things are required together and the cookbook's `makeShaderMaterial` does all three:
`fog: true` on the material, `THREE.UniformsLib.fog` merged into `uniforms`, and
`#include <fog_pars_fragment>` plus `#include <fog_fragment>` (fog **last** in `main`, after
tone mapping and colour space, matching three's own materials). For an additive glow, fade
through alpha instead — `gl_FragColor.a *= 1.0 - smoothstep(fogNear, fogFar, vFogDepth);` — or
the light gets brighter with distance.

Deliberate exception: a sky dome, or any material that genuinely must not fog. The static audit
opts out on the whole file if it contains `fog: false`, the comment marker `3dcv: no-fog`, or
the word `sky`; the runtime audit exempts sky-named and backside-no-depth-write materials.
Use the marker, do not fight the warning.

## Three warnings from the wider three.js world that do NOT apply here

Verified against this runtime on 2026-08-25 — believing them costs a repair round:

1. **"Always add the `logdepthbuf_*` chunks or your effect vanishes behind geometry."**
   `runtime_js/lib/browser/renderer.js` takes `logDepth` and it **defaults to false**; the only
   switch is `render_scene.mjs --log-depth`, and nothing in `codeverse/` ever passes it. So the
   logarithmic depth buffer is **off in every harness render**. Keep the cookbook's chunks —
   they compile to nothing without `USE_LOGDEPTHBUF` and would be needed if it were ever turned
   on — but they can never be the cause of what you are looking at. Do not spend a round there.
2. **"Guard against the GTAO / post-processing pass overriding your material."**
   There is no `EffectComposer`, no GTAO and no post-processing anywhere in `runtime_js`; the
   default pipeline renders straight to the canvas. The failure mode (instanced quads collapsing
   to the origin in the override-material pass) **cannot occur here**.
3. **"Patch chaining is safe."** It is not, and this one is real: `chunk_dropped` (WARN) fires
   when a `.replace('#include <x>', ...)` does not put `#include <x>` back. Always prepend the
   original include. Two differently patched `MeshStandardMaterial`s also share one compiled
   program unless each sets its own `customProgramCacheKey` — nothing detects that; you just get
   two identical-looking materials.

## What nothing detects

The gate proves your shader **compiles and is bound**. It says nothing about whether it is
*visible* or *right*: colours crushed by the ACES plus sRGB output chain, transparent water
z-fighting the shore, a patched material whose clone lost its `onBeforeCompile`, a shared
program cache key, an effect placed outside every camera's frustum. Nothing in the harness
renders the scene without your shader to compare against, so look at the frames yourself:
`scene_views` (or `render_views`) at two different times, and check that the thing you wrote
changed something.

Full audit source lines, the fix-hint texts, and a minimal citizen shader:
`references/shader-gate.md`.
