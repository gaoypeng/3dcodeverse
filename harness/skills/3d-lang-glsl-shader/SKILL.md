---
name: 3d-lang-glsl-shader
description: "Use when writing, refining or repairing a graphics-track fragment shader (language glsl_shader): the files, the uniform block the build prepends (never redeclare it), the two entry points, the gl-frames gate thresholds (NaN, black/blown, static, flicker, low detail) with their score caps, the lint's GLSL 330 rules, the feedback buffer, the judge's named defects, and the 3dcode loop."
license: Apache-2.0
compatibility: GLSL 330 core compiled with moderngl headless by the 3dcode build; frames at t = 0, 1, 2.5, 4, 6 s at the plan's resolution.
metadata:
  verified: "2026-11-07"
---

# GLSL fragment shader — graphics

You write ONE Shadertoy-style image pass; the build wraps it, compiles it (OpenGL 3.3 core,
headless), renders the judged frames and a preview GIF up to `duration_s`, measures them and
shows them to the judge.  Every frame is a pure function of the pixel and absolute `u_time`
(plus the samplers); there is no state between pixels.

## Files
```
src/shader.frag     REQUIRED  the image pass (no #version, no uniform/out declarations)
src/common.glsl     optional  helpers, pasted ABOVE shader.frag (and buffer_a.frag) at build time
src/buffer_a.frag   optional  ONE feedback buffer pass, rendered every frame before the image pass
```
Nothing else is read (another file under `src/` is a lint WARN `stray_file`).
`references/interface.md` is the interface, executable.

## What the build prepends (writing these yourself is a lint ERROR)
```glsl
#version 330 core
uniform float u_time;         // seconds; judged frames at t = 0, 1, 2.5, 4, 6 s
uniform vec2  u_resolution;   // pixels
uniform vec2  u_mouse;        // always (0,0) headless
uniform int   u_frame;        // frame counter — NOT a clock: the sampled times are not evenly spaced
uniform sampler2D u_prev;     // previous frame of THIS pass; black at frame 0
uniform sampler2D u_noise;    // 256x256 RGBA white noise, repeat, linear: one fetch is a bilinear hash
uniform sampler2D u_buffer_a; // output of src/buffer_a.frag (only when that file exists)
#define iTime u_time          // Shadertoy aliases: iTime iResolution iFrame iMouse iChannel0(=u_prev) iChannel1(=u_noise)
out vec4 fragColor;           // the ONLY output
```
NOT available: iChannel2/3, iDate, iSampleRate, iChannelResolution, textures, images, sound.

## Entry point (one of the two)
`void mainImage(out vec4 fragColor, in vec2 fragCoord)` (preferred) or `void main()` using
`gl_FragCoord.xy`.  Pixel coordinates, origin BOTTOM-LEFT, y up (the build flips rows when it
writes PNGs).  `u_prev` / `u_buffer_a` are sampled at `fragCoord / u_resolution`.  Circles must
stay round at the plan's resolution (judge defect `stretched_aspect`, −0.06): normalise both
axes by `u_resolution.y`.

## What is measured (`3dcode gl-frames`; the cap is the highest score the round can get)
| measured | threshold | finding | cap |
|---|---|---|---|
| any NaN / Inf pixel | ≥ 1 | `nan` ERROR | 0 |
| every frame black | > 97 % of pixels under luminance 0.03 in each frame | `black` ERROR | 0.5 |
| every frame blown | > 97 % over 0.98 in each frame | `blown` ERROR | 0.5 |
| largest mean \|Δ\| between consecutive frames | < 0.002 (plan `motion` non-empty) | `static` WARN | 0.5 |
| largest mean \|Δ\| between consecutive frames | > 0.35 | `flicker` WARN | 0.7 |
| mean edge density | < 0.002 | `low_detail` WARN | none (the judge is told) |
| any lint ERROR | | | 0.6 |
| build failure (compile error, render timeout) | | | 0 |
NaN sources: division by zero, sqrt / pow / log of a negative, normalize of a zero vector,
acos / asin outside [−1, 1].  An unmet `must` acceptance item caps at 0.7.

## The lint (ERROR unless noted)
* GLSL 330 core: `texture()` not `texture2D`; `fragColor` not `gl_FragColor`; no `varying` /
  `attribute`; `%` is integer-only — `mod(x, y)` on floats.
* No uniform declarations (yours would never be set — use `const`), no `out`, no `#version`,
  no `#include`; `precision` is ignored (INFO).  Only `u_prev`, `u_noise`, `u_buffer_a` may be
  sampled.  `common.glsl` must not define `main()` / `mainImage()`; inside `buffer_a.frag` the
  previous buffer is `u_prev` (sampling `u_buffer_a` there is a WARN).  > 60 000 characters WARN.

## Build errors
`src/shader.frag:LINE: error: …` with YOUR line numbers.  "no matching function": an int
literal where a float is expected (`pow(2, 3)`, `mod(x, 2)`) — write `2.0`; "too few
components": `vec4(col)` of a vec3 needs `vec4(col, 1.0)`; "redeclaration": a harness uniform
declared again; "undeclared": a typo or a helper not in `common.glsl`.  Loop bounds are
compile-time constants (break early inside); `smoothstep(a, b, x)` is undefined for a ≥ b; a
frame that does not finish inside the render timeout fails the build.

## Feedback and determinism
`u_prev` is THIS pass's previous frame (black at frame 0); `buffer_a.frag` reads its own
previous output as `u_prev` (neighbours at `uv ± 1.0 / u_resolution`) and `shader.frag` reads
it as `u_buffer_a`.  An accumulation with gain ≥ 1 saturates by t = 6 s (`blown`).  No
per-frame random seeds, nothing that depends on `u_frame` alone (`flicker`).

## What the judge (`shader`) names
Likeness (0.26): the wrong physics scores 0.4 however complete the inventory — evenly spaced
bars standing in for curtains, rays, flames or waves (`comb_artefact`, −0.10, cap 0.65);
hard-edged opaque shapes standing in for light — bokeh, glow, aurora, neon
(`opaque_shapes_for_light`, −0.08); a few sparkles for a star field; a flat wash for a nebula.
Depth and detail (0.16): one texture or noise field filling the frame is `flat_fill` (−0.08);
staircase edges `banding_or_aliasing` (−0.05).  Light and tone (0.16): a night subject with
nothing near-black `no_dark_range` (−0.06); haze lifting a region toward white `white_wash`
(−0.08); uniformly candy-saturated colour `candy_saturation` (−0.06).  Motion (0.14): a texture
scrolling under a fixed shape is 0.5.  A textbook demo with the brief's colours swapped in is
`placeholder_look` (−0.10); an effect present in source but invisible in the frame is
`wrong_subject` (cap 0.40) — check the final `fragColor` write, the raymarch hit test, the ray
direction, a term multiplied to zero.

## The loop
write → `3dcode gl-probe` (compiles? one frame at t = 1 s; `--t` for another) → fix everything
it reports → repeat → `3dcode gl-frames` → OPEN the sheet and compare with the plan's key visuals
→ `3dcode check` clean.  Do not finish while `gl-probe` reports anything: measured on a ten-rung
ladder, sessions that ran this loop compiled 10 of 10, sessions without it 5 of 10.  Clear NaN,
black, blown and static before adding the next layer; deleting a layer is a legitimate fix.
