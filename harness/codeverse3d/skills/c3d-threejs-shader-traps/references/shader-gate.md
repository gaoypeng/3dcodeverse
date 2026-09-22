# The shader gate, in full

Read out of the live runtime on 2026-08-25: `runtime_js/lib/glsl_audit.mjs`,
`runtime_js/lib/host_compile.mjs`, `runtime_js/lib/shader_report.mjs`,
`runtime_js/lib/scene_host.mjs`, `runtime_js/lib/browser/renderer.js`,
`runtime_js/check_shaders.mjs`, `runtime_js/package.json`, and
`codeverse3d/spatial/probes.py`. Renderer: `three@0.182.0`, headless WebGL through
`puppeteer@^24.43.1`.

## 1. How a finding reaches you

```
src/**.js  --(regex-extracted GLSL strings)-->  glsl_audit.auditFile   -> errors[] / warnings[]
booted page -> window.__c3v.compileAll()      -> shader_errors[]       -> errors[] (mapped to file:line)
                                              -> materialAudit(scene)  -> errors[] / warnings[]
                                              -> console noise         -> warnings[]
                                    all merged by shader_report.compileIntoReport
                                    -> codeverse3d/spatial/probes.py::shader_report
                                    -> GateReport(gate="shader_preflight")
```

`passed` is `report.ok and no ERROR finding`. The agent-facing tool is `shader_probe`; it runs
the build's own probe and preflight (one `probe_scene.mjs --compile` boot), so its verdict is
the build's, and every `scene_threejs` round carries the report among its gates.

A compile error carries `stage`, `material`, the offending `source_line`, surrounding
`context`, and a `fix_hint` derived from the driver message. When the same GLSL line appears in
several files the message says `(N identical lines; first shown)` — rename or de-duplicate
rather than guessing.

## 2. The static audit, rule by rule

Source: `glsl_audit.mjs::auditFile(file, source, { sceneUsesFog })`. Default severity is
`error`; only the rules marked WARN below pass `'warn'`.

| kind | severity | trigger (as coded) | message / fix |
|---|---|---|---|
| `include_not_alone` | ERROR | a line matching `#include` that is not the bare include form and has no `${` in it | "'#include chunk' must be ALONE on its line" |
| `version_directive` | ERROR / WARN | `^\s*#version` — ERROR in a `ShaderMaterial`, WARN in a `RawShaderMaterial` | three prepends its own `#version`; a second one is a compile error |
| `precision_directive` | WARN | `^\s*precision (lowp|mediump|highp) float;` and not Raw | three prepends precision for `ShaderMaterial`; delete yours |
| `fragment_out_and_gl_fragcolor` | ERROR | the string has an `out vec4` declaration and writes `gl_FragColor` | keep one: `gl_FragColor` with no `out`, or GLSL3 with your own out variable |
| `glsl3_gl_fragcolor` | ERROR | `glslVersion: THREE.GLSL3` in the file and `gl_FragColor` in a fragment-looking string | GLSL3 removes `gl_FragColor`; declare `out vec4 fragColor;` |
| `undeclared_uniform` | ERROR | any string contains `uTime` and none contains `uniform float uTime;` | "a JS uniforms entry is not a GLSL declaration" |
| `unbound_uniform` | ERROR | any string contains `uTime` and the source **outside every template literal** has no `uTime:` or `uTime =` | `uniforms: { uTime: { value: 0 } }` and update `.value` in `update(t)` |
| `chunk_dropped` | WARN | a `.replace('#include <X>', repl)` whose `repl` does not itself contain `#include <X>` | prepend the original include so fog / logdepth / shadow code still runs |
| `no_fog` | WARN | the scene uses fog, the file has `ShaderMaterial`, is not Raw, has no opt-out, and a `void main` string writes a colour without `fog_fragment` / `USE_FOG` / `fogColor` | add the fog chunks, `fog: true` and `UniformsLib.fog` |

A "fragment-looking" string is one containing any of `gl_FragColor`, `gl_FragCoord`,
`csm_FragColor`, `pc_fragColor`, `fragColor`, `output_fragment`, `discard` **and** no
`gl_Position`.

The `no_fog` opt-out is file-wide: `fog: false`, the comment marker `3dcode: no-fog`, or the word
`sky` anywhere in the file suppresses it. Prefer the explicit marker — the accidental `sky`
match will also silence a shader you did want fogged.

## 3. The runtime audit, exactly

`host_compile.mjs::materialAudit(scene, THREE)` traverses the booted scene, visits each distinct
`isShaderMaterial` once, and emits:

```js
skyLike = /sky|dome|stars|cloud|sun|moon/i.test(`${object.name} ${material.name}`)
       || (material.side === THREE.BackSide && material.depthWrite === false);

if (scene.fog && !skyLike
    && !(material.fog && material.uniforms && material.uniforms.fogColor)
    && !/fog_fragment|fogColor/.test(fragmentShader))
      -> { kind: 'no_fog', severity: 'warn' }

if (/\buTime\b/.test(fragmentShader + vertexShader) && !material.uniforms?.uTime)
      -> { kind: 'unbound_uniform', severity: 'error' }
```

Note what this means in practice: the runtime pass sees the material you actually built, so it
catches the case the static pass cannot — a `uniforms` object assembled at runtime that ends up
without `uTime`, or a material cloned from a patched one. Conversely it only inspects
`isShaderMaterial`, so an `onBeforeCompile` patch on a `MeshStandardMaterial` is invisible to it
and only the static `chunk_dropped` rule protects you.

## 4. A minimal citizen shader

Full boilerplate: `makeShaderMaterial(opts)` in `src/lib/shader.js` (scene workspaces). The
smallest thing that passes every rule above:

```js
const VS = `
#include <common>
#include <fog_pars_vertex>
varying vec2 vUv;
void main() {
  vUv = uv;
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;
  #include <fog_vertex>
}`;

const FS = `
#include <common>
#include <fog_pars_fragment>
uniform float uTime;
uniform vec3 uTint;
varying vec2 vUv;
void main() {
  float w = 0.5 + 0.5 * sin(vUv.x * 12.0 + uTime * 1.3);
  gl_FragColor = vec4(uTint * (0.6 + 0.4 * w), 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #include <fog_fragment>
}`;

export function makeRipple(THREE, tint = 0x3a6ea5) {
  const mat = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([
      THREE.UniformsLib.fog, { uTime: { value: 0 }, uTint: { value: new THREE.Color(tint) } }]),
    vertexShader: VS, fragmentShader: FS, fog: true,
  });
  mat.name = 'Ripple';
  mat.userData.update = (t) => { mat.uniforms.uTime.value = t; };
  return mat;
}
```

Every include is alone on its line; no `#version`, no `precision`; `uTime` is both declared in
GLSL and bound in JS; the fog chunks are present and `fog_fragment` is last. Collect the
materials once at build time and call `m.userData.update(t)` from `update(t, dt)` — never
traverse the scene inside `update`, and never read the clock from `Date.now()`.

## 5. Corpus, and why this bundle is `mixed` rather than `measured`

`bench/out` holds **4** `scene_threejs` runs (8 judged rounds). Their `shader_preflight`
reports are the only ones in the corpus and **all four are clean**:

```
scn_easy_rooftop_garden   info  compiled 10 programs (1 custom)  in 107 ms
scn_easy_desert_canyon    info  compiled  9 programs (12 custom) in  38 ms
scn_easy_japanese_garden  info  compiled 14 programs (2 custom)  in  16 ms
scn_easy_greenhouse       info  compiled 11 programs (2 custom)  in 825 ms
```

Zero errors, zero warnings, 1-12 custom materials each. So the detection map above is measured
from the harness source, but the *frequency* of these defects here is unknown, and this bundle
must not claim otherwise. What the same four runs do show is the failure the shader work is
supposed to prevent: `animation_life` 0.431 and `technical_cleanliness` 0.475, the two lowest
criteria of any language in the corpus, with judge issues reading "No animation is visible; the
scene is completely static" and "No visible movement in plants or string lights between time
samples". A shader that compiles cleanly and never moves scores exactly like no shader at all.

Revisit this file when `scene_threejs` reaches 20 graded runs; if `shader_preflight` is still
silent, the honest conclusion is that the *static* rules are doing their job before the corpus
ever sees them, and the bundle's weight should shift further towards section 4.
