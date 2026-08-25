---
name: cv3d-opengl-pipeline
description: Write a raw OpenGL / moderngl program that renders what the brief asked for. Use for any opengl_python session - baseline, refine, repair or rebuild - and whenever the gl_frames gate reported flicker, no motion or a near-flat image. Covers the setup / render contract, the sampled-time trap that breaks every feedback simulation, VAO and uniform mechanics, ping-pong FBOs, and the three richness rules the judge scores this language worst on.
license: Apache-2.0
compatibility: OpenGL 3.3 core through moderngl, headless EGL; one module src/program.py, GLSL 330 core inline or in src/*.glsl.
metadata:
  evidence: mixed
  evidence_note: 'Thin but real, and labelled so in the body. 5 graded opengl_python runs across graphics_v1_flash and graphics_v2_flash, 10 judged rounds, recomputed 2026-08-25. The API facts come from the runtime wrapper and the lint, not from the corpus.'
  verified: "2026-08-25"
---

# The moderngl pipeline, and the two things that actually sink these runs

## The contract in four lines

`setup(ctx, width, height) -> state` is called **once**. `render(ctx, state, t, frame, fbo)`
is called once per sampled time; `fbo` arrives already bound, cleared to black with depth 1,
and with the viewport set. If you render into your own framebuffers you must call
`fbo.use()` and reset `ctx.viewport` before the **final** pass — the harness reads that
framebuffer and nothing else. Never create a context and never touch `ctx.screen`.

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

The fix is to make the frame a pure function of `t`, and get the iterations inside
`render()`:

* pick a fixed simulation step, e.g. `DT = 1/120`, and run `n = round(t / DT)` steps;
* keep the state and the time it represents in `state`; each call advances from
  `state["t_sim"]` to `t`, and **resets to the seed whenever `t < state["t_sim"]`** so an
  out-of-order or repeated sample still gives the same image;
* cap the work per call (a few thousand steps) so the build does not time out;
* inject sources as a function of `t`, not once at `frame == 0`.

## Trap 2: motion has to be continuous *and* visible

`gl_frames` measures the mean absolute difference between consecutive frames. Below
**0.002** it calls the program static; above **0.35** it calls it flicker. Only one of our
five opengl_python runs ever tripped this gate, but it tripped it in three separate rounds
(max deltas 0.373, 0.392, 0.416, `ogl_hard_voxel_city`, final score 0.342), and the judge
read the same frames as *"the camera motion is discontinuous or clips into a building,
resulting in a hard cut"*. Since the samples are 1 to 2 s apart, a camera or a phase that
is fine at 60 fps can still jump between samples. Drive every animated quantity
from a smooth function of `t`, and check the amplitude *between the sampled times*, not per
frame: `fract()`/`mod()` wraps and per-call randomness are the usual culprits.

## API mechanics that cost a build

* **Attribute names must match the shader's `in` names**, and the format string must match
  the byte layout: `[(vbo, "3f 3f", "in_pos", "in_normal"), (ibo, "3f 3f 1f/i", ...)]`. A
  wrong float count does not raise — it silently shuffles your data into garbage.
* **numpy is row-major, GLSL is column-major**: `prog["u_mvp"].write(M.T.astype("f4").tobytes())`.
  Never `.value = m.tolist()`.
* **`KeyError: 'u_x'`** means the compiler removed an unused uniform. Delete the assignment
  or guard with `if "u_x" in prog`.
* **Your own FBOs need their own depth attachment and their own `.clear()`** — `ctx.clear()`
  clears whatever is bound. Disable `DEPTH_TEST` for full-screen passes.
* **Ping-pong needs two textures**; never sample the one you are writing. Give a simulation
  state texture `NEAREST` filtering and clamped wrap — `LINEAR` diffuses the state a little
  every step and `repeat` wraps the domain — and store it as `f4`, not `f2` (half float
  clips near 65504 and quantises visibly in a feedback loop).
* Textures need `.use(location=k)` **every frame** as well as `prog["u_tex"].value = k`.
* `#version 330 core` on the first line of every shader string, `out vec4 fragColor;`, no
  `gl_FragColor`, no `attribute`/`varying`. Wide lines and `GL_QUADS` do not exist in core.
* Seed with `np.random.default_rng(SEED)` in `setup`; no wall clock, no extra python module
  (the harness ignores anything but `src/program.py`, and the lint warns).

## Make the image worth judging

Over 5 runs and 10 judged rounds (`graphics_v1_flash` + `graphics_v2_flash`, recomputed
2026-08-25) this language's three weakest criteria are **originality 0.330**,
**visual_richness 0.375** and **brief_fidelity 0.420** — the lowest three numbers anywhere
in our corpus. The gate agrees from the other side: edge density below **0.002** is
reported as *"a near-flat gradient"*.

Three rules that answer exactly those readings:

1. **Layer at least three depths.** Background, subject, foreground. One full-screen
   gradient with a shape on it is the flat-gradient finding waiting to happen; parallax
   between layers also supplies the frame-to-frame delta.
2. **No single primitive carrying the frame.** Edge density comes from silhouettes and
   high-frequency shading — instanced geometry, particles, structure inside surfaces — not
   from a smooth ramp.
3. **Spend the brief's own nouns.** `brief_fidelity` 0.420 says the frames were generic.
   Every named element in the brief should be findable in the image; build the named thing
   rather than an abstraction that gestures at it.

## Prove it

`gl_probe` after every edit (one frame, compile errors with source line numbers), then
`gl_frames` before finishing — it renders the judged times, returns the per-frame
luminance / colour / detail / motion table and runs the gate. **Look at the contact sheet.**

Code to copy: cookbook `opengl_python` sections *VBO / VAO with interleaved attributes*,
*Instancing*, *Uniforms and matrices*, *FBOs*, *PITFALLS*.

A worked sampled-time feedback loop and the frame-metric table: `references/sampled-time.md`.
