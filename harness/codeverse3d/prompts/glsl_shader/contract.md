# glsl_shader authoring contract (Shadertoy-style fragment shader, GLSL 330 core)

You write ONE fragment-shader body; the harness wraps it, compiles it with moderngl (OpenGL 3.3 core,
headless), renders frames at the plan's resolution for t = 0, 1, 2.5, 4, 6 s (plus a preview GIF up to
`duration_s`) and measures / judges those frames.  The code is the deliverable.  This file states the
interface and what is measured; what the frames show is the brief's business.

## Files
```
src/shader.frag     REQUIRED  the image pass (your code; no #version, no uniform/out declarations)
src/common.glsl     optional  helper functions / constants, pasted ABOVE shader.frag (and buffer_a.frag) at build time
src/buffer_a.frag   optional  ONE feedback buffer pass (Shadertoy "Buffer A"): rendered every frame before the image pass
```
Nothing else is read — no textures, files or includes; any other file under `src/` is a lint WARN
(`stray_file`) and ignored.  The skeleton's `src/common.glsl` is starter text you may keep, change or delete.

## What the harness prepends (DO NOT write these lines yourself — redeclaring them is a lint ERROR)
```glsl
#version 330 core
uniform float u_time;         // seconds; the judged frames are at t = 0, 1, 2.5, 4, 6 s (preview GIF up to duration_s)
uniform vec2  u_resolution;   // pixels, e.g. 1280x720
uniform vec2  u_mouse;        // always (0,0) headless — do not depend on it
uniform int   u_frame;        // frame counter — NOT a clock: the sampled times are not evenly spaced
uniform sampler2D u_prev;     // previous frame of THIS pass (feedback / trails); black at frame 0
uniform sampler2D u_noise;    // 256x256 RGBA white noise, repeat, linear: one fetch is a bilinear-interpolated hash
uniform sampler2D u_buffer_a; // output of src/buffer_a.frag (only when that file exists)
#define iTime u_time          // Shadertoy aliases: iTime iResolution iFrame iMouse iChannel0(=u_prev) iChannel1(=u_noise)
out vec4 fragColor;           // the ONLY output
```
NOT available: iChannel2/3, iDate, iSampleRate, iChannelResolution, textures/images/videos/sound.

## Entry point (one of the two; the harness detects which)
```glsl
void mainImage(out vec4 fragColor, in vec2 fragCoord) { ...; fragColor = vec4(col, 1.0); }   // preferred
// or:  void main() { ...; fragColor = vec4(col, 1.0); }   using gl_FragCoord.xy
```
`fragCoord` / `gl_FragCoord.xy` are PIXEL coordinates, origin BOTTOM-LEFT, y up (the harness flips rows when
it writes PNGs, so what you compute is what you see).  `u_prev` / `u_buffer_a` are sampled at
`fragCoord / u_resolution` (0..1).  Circles must stay round at the plan's resolution — the judge's
`stretched_aspect` defect (−0.06): e.g. normalise both axes by `u_resolution.y`.

## What is measured (the `gl_frames` gate on the judged frames, then the `shader_v2` judge)
* A NaN / Inf pixel in any frame → ERROR, score cap 0.  NaN sources in GLSL: division by zero, sqrt / pow /
  log of a negative, normalize of a zero vector, acos / asin outside [-1, 1].
* Every frame essentially black (more than 97 % of its pixels under luminance 0.03) or blown (more than 97 %
  over 0.98) → ERROR, cap 0.5.
* When the plan's `motion` is non-empty: the largest mean |Δ| between consecutive sampled frames under 0.002 →
  `static` WARN, cap 0.5.  A brief that asks for a static image is exempt.
* A consecutive pair with mean |Δ| over 0.35 → `flicker` WARN, cap 0.7.
* Mean edge density under 0.002 → `low_detail` WARN (no cap; the judge is told).
* Any lint ERROR → cap 0.6; a build failure (compile error, render timeout) → 0.
* The judge grades likeness 0.26, depth_and_detail 0.16, light_and_tone 0.16, motion_quality 0.14,
  composition 0.10, technical_cleanliness 0.10, craft 0.08, with the defect list the system prompt names.

## Rules the lint enforces (ERROR unless noted)
* GLSL 330 core: `texture()` not `texture2D`; `fragColor` not `gl_FragColor`; no `varying` / `attribute`;
  `%` is integer-only — `mod(x, y)` on floats.
* No uniform declarations (the harness's are redeclarations; your own would never be set — use `const`),
  no `out` variable, no `#version`, no `#include`; a `precision` qualifier is ignored (INFO).
* Only `u_prev`, `u_noise`, `u_buffer_a` (iChannel0 / iChannel1) may be sampled.
* `src/common.glsl` must not define `main()` / `mainImage()`.  Inside `src/buffer_a.frag` the previous buffer is
  `u_prev`; sampling `u_buffer_a` there is a WARN.
* A file over 60 000 characters is a WARN.

## Build errors
They come back as `src/shader.frag:LINE: error: …` (line numbers refer to YOUR file).  Language facts behind
the common messages: "no matching function" — an int literal where a float is expected (`pow(2, 3)`,
`mod(x, 2)`): write `2.0`; "too few components" — `vec4(col)` of a vec3 needs `vec4(col, 1.0)`;
"redeclaration" — a harness uniform declared again; "`x' undeclared" — a typo or a helper that is not in
`src/common.glsl`.  Loop bounds must be compile-time constants (break early inside); `smoothstep(a, b, x)` is
undefined for a ≥ b.  The render has a wall-clock timeout: a frame that does not finish fails the build.

## Feedback (`u_prev` / `src/buffer_a.frag`)
`u_prev` is black at frame 0 and is THIS pass's previous frame.  `src/buffer_a.frag` reads its own previous
output as `u_prev` (its neighbours are at `uv ± 1.0 / u_resolution`) and `src/shader.frag` reads the buffer's
output as `u_buffer_a`.  An accumulation whose gain is ≥ 1 saturates by t = 6 s and trips `blown` (cap 0.5).

## Determinism
Every frame is a function of the pixel, `u_time` and the samplers only: no per-frame random seeds (the
`flicker` measure above), nothing that depends on `u_frame` alone.

## Interface example — `src/shader.frag` (the names in use; lints clean, builds, moves — what it draws is not a suggestion)
```glsl
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution) / u_resolution.y;    // centred, y up, circles stay round
    float n = texture(u_noise, p * 0.5 + 0.02 * u_time).r;          // the noise texture as a hash
    float d = length(p - vec2(0.3 * cos(u_time), 0.0));
    vec3 col = vec3(0.2, 0.3, 0.5) * (0.5 + 0.5 * n) + vec3(1.0, 0.8, 0.5) * exp(-8.0 * d);
    fragColor = vec4(col, 1.0);
}
```
