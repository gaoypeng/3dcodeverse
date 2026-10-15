---
name: c3d-opengl-pipeline
description: "Use when writing an opengl_python / moderngl program, or when the gl_frames gate reported flicker, no motion or a near-flat image. Covers baseline, refine, repair and rebuild sessions: the sampled-time trap that breaks every feedback simulation, what the frame gate and the judge measure on this language (edge density, motion delta, the acceptance items), and a few optional practices — full-screen passes, instancing, textures, your own framebuffers, linear light, GL state."
license: Apache-2.0
compatibility: OpenGL 3.3 core through moderngl, headless EGL; one module src/program.py, GLSL 330 core inline or in src/*.glsl.
metadata:
  evidence: mixed
  evidence_note: 'Thin but real, and labelled so in the body. 5 graded opengl_python runs across graphics_v1_flash and graphics_v2_flash, 10 judged rounds, recomputed 2026-08-25. The API facts come from the runtime wrapper and the lint, not from the corpus.'
  verified: "2026-10-15"
  target_metric: "gl_frame_findings"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "0.20 mean / 0.0 median findings per run; n=5 (bench/out, last gated round, 2026-08-25); 4 of 5 at zero"
---

# The moderngl pipeline, and the two things that actually sink these runs

Interface, sandbox, the moderngl build facts and the gate thresholds are in the language
contract (`setup` / `render`, the harness `fbo`, the lint lists, the caps); this skill is the
craft on top of it, and nothing below is a required shape.

## Trap 1: `render()` is sampled, not ticked

The judged times are **t = 0, 1, 2.5, 4 and 6 s** — five calls, with unequal gaps, and with
about a dozen more interleaved when the preview GIF is on (so `frame` indexes the merged
sorted schedule, not the judged frames). There is no `dt` and there is no 60 Hz loop.

A simulation that advances one step per `render()` call therefore gets **five steps in
total**. This is not hypothetical: two of our 5 graded opengl_python runs were ping-pong
feedback effects, and they scored **0.19 and 0.48** (`graphics_v2_flash`, mined
2026-08-25) - the lowest and third-lowest of the five. The judge wrote *"the interior of
the seeds dies out"*, *"only expanding rings rather than labyrinthine patterns"*, *"ink is
only injected at t=0s"* and *"the ink dissipates far too quickly"*. Those are all the same
defect: five iterations of a solver that needed hundreds.

The requirement: the frame at `t` must not depend on which `t`s were rendered before (the
tools re-render single times, and the build renders a sorted schedule). One strong way to
meet it with a simulation: keep the state and the simulated time it represents in `state`;
each call integrates from that time to `t` with a fixed step and a bounded step count, and
resets to the seed whenever `t` is earlier than the state; inject sources as a function of
the simulated time, not in a `frame == 0` branch. A worked description: `references/sampled-time.md`.

## Trap 2: motion has to be continuous *and* visible

`gl_frames` takes the mean |Δ| of each consecutive pair of sampled frames: if the largest is
below **0.002** the program is static (cap 0.5); if it exceeds **0.35** it is flicker (cap 0.7).
Only one of our five opengl_python runs ever tripped this gate, but it tripped it in three
separate rounds (max deltas 0.373, 0.392, 0.416, `ogl_hard_voxel_city`, final score 0.342),
and the judge read the same frames as *"the camera motion is discontinuous or clips into a
building, resulting in a hard cut"*. Since the samples are 1 to 2 s apart, a camera or a
phase that is fine at 60 fps can still jump between samples. Check the change between the
sampled times, not per frame: periodic wraps and per-call randomness are what has tripped
the threshold.

## What the gate and the judge measure on the image

Over 5 runs and 10 judged rounds (`graphics_v1_flash` + `graphics_v2_flash`, recomputed
2026-08-25) this language's three weakest criteria were **originality 0.330**,
**visual_richness 0.375** and **brief_fidelity 0.420** — the lowest three numbers anywhere
in our corpus. What is measured behind them:

* mean edge density over the judged frames — below **0.002** the gate reports *"a near-flat
  gradient"* (`low_detail`, no cap; the judge sees the same frames) — and the frame-to-frame
  delta above. Structure at more than one scale supplies both: silhouettes, instanced or
  particle detail, texture inside surfaces. Parallax between elements at different depths is
  one way to get the delta, not a required composition.
* the acceptance items and key visuals of the plan — the judge checks each one against the
  frames, and an unmet `must` item caps the score at 0.7. Build the named things so each is
  findable in the judged frames; an abstraction that gestures at the brief scores as generic.

## Practices (strong options, none required)

**A full-screen pass.** Four clip-space vertices in a `ctx.buffer`, a `ctx.vertex_array` bound
with `"2f"` to the vertex shader's `in vec2`, drawn as a triangle strip; the vertex shader
derives uv from the clip position. A purely 2D brief can be one such pass driven by uniforms
you set from `render` — a legitimate option, not the expected shape. Full-screen passes with
DEPTH_TEST on draw nothing behind the previous depth: disable it for them.

**Instancing.** A second buffer bound with `/i` formats and `vao.render(instances=n)` draws
many copies in one call. A per-instance phase attribute lets the vertex shader animate each
copy from `u_time` with no per-frame upload, which keeps the cost of a call flat under the
build timeout; CPU-side updates go through `buffer.write(...)` at the same byte size
(`buffer.orphan()` before a full rewrite).

**Textures.** Procedural data from a seeded `np.random.default_rng(seed)` goes into
`ctx.texture((w, h), components, data, dtype="f4")` or `ctx.texture3d(...)`; set `.filter` and
`.repeat_x` / `.repeat_y` explicitly, and bind with `.use(location=k)` on every call before the
draw that samples it.

**Your own framebuffers.** `ctx.framebuffer(color_attachments=[tex], depth_attachment=...)`
with a `ctx.depth_renderbuffer` when the pass needs occlusion. Ping-pong needs two textures —
never sample the one you are writing. Give a simulation state texture `NEAREST` filtering and
clamped wrap (`LINEAR` diffuses the state a little every step and `repeat` wraps the domain)
and store it as `f4`, not `f2` (half float clips near 65504 and quantises visibly in a
feedback loop). Several outputs from one pass are fragment-shader out variables with a
layout location each, one colour attachment per location.

**Light in linear, tonemap once.** Keep lighting and accumulation in linear HDR targets
(`f2` / `f4`) and apply one tonemap plus gamma in the final pass into `fbo`; that is also what
keeps the `blown` gate quiet when glow sums grow. With non-uniform scale, transform normals
by the inverse-transpose of the model matrix. For a physically based surface a GGX / Schlick
direct term in linear light is the usual choice — `c3d-glsl-craft` describes it; nothing is
seeded into this language's workspace.

**GL state.** The enables and blend modes you are likely to reach for:
```python
ctx.enable(moderngl.DEPTH_TEST); ctx.disable(moderngl.DEPTH_TEST)
ctx.enable(moderngl.BLEND); ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
ctx.blend_func = moderngl.ONE, moderngl.ONE            # additive glow
ctx.enable(moderngl.CULL_FACE); ctx.front_face = "ccw"
ctx.enable(moderngl.PROGRAM_POINT_SIZE)                # gl_PointSize in the vertex shader, mode=moderngl.POINTS
```

## Prove it

`gl_probe` after every edit (one frame, compile errors with source line numbers), then
`gl_frames` before finishing — it renders the judged times, returns the per-frame
luminance / colour / detail / motion table and runs the gate. **Look at the contact sheet.**

How the schedule is built and what the gate measures: `references/sampled-time.md`.
