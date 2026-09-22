# Sampled time: how a feedback effect survives five render calls

## What the harness does

`codeverse3d/languages/opengl_python/wrappers/run_gl.py` builds one sorted schedule from the
judged times and the preview times, then for each entry binds the harness framebuffer,
clears it to `(0, 0, 0, 1)` with depth 1, sets the viewport, and calls
`render(ctx, state, t, i, fbo)`. `i` is the index into that merged schedule.

Judged times are `0, 1, 2.5, 4, 6` (`codeverse3d/languages/_gl_common.py`, clipped to the
loop duration, always at least 3 samples). The preview GIF adds about a dozen more. So:

* the number of calls is not fixed, and `frame == 2` is not "the third judged frame";
* the gaps between judged frames are 1.0, 1.5, 1.5 and 2.0 seconds;
* nothing guarantees the calls are the only ones, or that they will not be repeated.

The only safe contract is the one the language contract already states: **the same `t` must
produce the same image.**

## The shape of a correct feedback loop

```python
DT = 1.0 / 120.0          # simulation step
MAX_STEPS = 4000          # keep the build inside its timeout

def _reset(state):
    state["read"].write(state["seed_bytes"])
    state["t_sim"] = 0.0

def _advance(ctx, state, t):
    if t < state["t_sim"] - 1e-6:
        _reset(state)
    n = min(MAX_STEPS, int(round((t - state["t_sim"]) / DT)))
    for _ in range(n):
        state["write_fbo"].use()
        state["read"].use(location=0)
        state["sim_vao"].render(mode=moderngl.TRIANGLE_STRIP)
        state["read"], state["write"] = state["write"], state["read"]
        state["read_fbo"], state["write_fbo"] = state["write_fbo"], state["read_fbo"]
    state["t_sim"] += n * DT
```

`render()` then calls `_advance(ctx, state, t)`, binds `fbo`, resets the viewport and draws
the display pass from `state["read"]`. Note what this buys: t = 0 gets the seed, t = 6 gets
720 steps instead of 5, and re-rendering an earlier time still produces the earlier image.

Sources (ink injections, seeds, disturbances) belong inside the step loop as a function of
the simulated time, not in a `if frame == 0:` branch — the judge explicitly marked one of
our runs down for *"ink is only injected at t=0s"*.

## State texture settings that matter

| setting | why |
|---|---|
| `dtype="f4"` | `f2` quantises visibly after a few hundred feedback steps and clips near 65504 |
| `filter = (NEAREST, NEAREST)` | `LINEAR` blurs the state slightly on every read; over hundreds of steps that is a diffusion term you did not ask for |
| `repeat_x = repeat_y = False` | otherwise the domain wraps and structures reappear on the opposite edge |
| two textures, swapped | reading and writing one texture in a single pass is undefined |

## The frame metrics the gate reads

`codeverse3d/spatial/frame_stats.py`:

| metric | threshold | finding |
|---|---|---|
| mean abs frame-to-frame delta | < 0.002 | static (WARN) |
| mean abs frame-to-frame delta | > 0.35 | flicker (WARN) |
| consecutive frames identical | delta < 1e-4 | duplicate (INFO) |
| mean edge density | < 0.002 | near-flat gradient (WARN) |
| mean luminance | < 0.03 all frames | black (ERROR) |
| mean luminance | > 0.98 all frames | blown (ERROR) |
| any NaN / Inf pixel | any | ERROR |

The gate's own advice for luminance is to aim for a mean between 0.2 and 0.6.

## Measured deltas from our own runs

`graphics_v1_flash` + `graphics_v2_flash`, 5 graded opengl_python runs, mined 2026-08-25.
The gate fired on exactly one of them - `ogl_hard_voxel_city`, final score 0.342 - but it
fired repeatedly: flicker in three separate rounds, with max frame-to-frame deltas of
0.373, 0.392 and 0.416, each barely over the 0.35 line, and each caused by a
discontinuity between two samples 1.5 to 2 seconds apart rather than by per-frame noise.
The same run also produced `edge density 0.0000`, an entirely smooth image. So the gate is
a weak signal at n=5; the judged criteria below are the stronger one.

Judged criterion means for this language over the same 10 rounds: originality 0.330,
visual_richness 0.375, brief_fidelity 0.420, motion_quality 0.510, composition 0.515,
technical_cleanliness 0.605, colour_light 0.610.
