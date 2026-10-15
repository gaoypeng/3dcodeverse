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
* nothing guarantees the calls are the only ones, or that they will not be repeated — the
  `gl_probe` and `gl_frames` tools re-render single times in any order.

The only safe contract is the one the language contract already states: **the same `t` must
produce the same image.**

## The shape of a correct feedback loop (one option, described, not prescribed)

Store the simulated time next to the state texture in `state`. On each call, if `t` is earlier
than that time, reset the state to its seed; then step from the stored time to `t` with a fixed
step and a bounded step count, each step drawing the read texture into the write framebuffer
and swapping the pair. Finally bind `fbo`, reset the viewport and draw the display pass from
the read texture. What this buys: the earliest sample shows the seed, the last sample the
fully integrated state, and re-rendering an earlier time gives the earlier image.

Sources (ink injections, seeds, disturbances) belong inside the step loop as a function of
the simulated time, not in a `frame == 0` branch — the judge explicitly marked one of
our runs down for *"ink is only injected at t=0s"*.

## State texture settings that matter

| setting | why |
|---|---|
| `dtype="f4"` | `f2` quantises visibly after a few hundred feedback steps and clips near 65504 |
| `filter = (NEAREST, NEAREST)` | `LINEAR` blurs the state slightly on every read; over hundreds of steps that is a diffusion term you did not ask for |
| `repeat_x = repeat_y = False` | otherwise the domain wraps and structures reappear on the opposite edge |
| two textures, swapped | reading and writing one texture in a single pass is undefined |

## The frame metrics the gate reads

`codeverse3d/spatial/frame_stats.py`, on the judged frames; the cap is the highest score the
round can then get:

| metric | threshold | finding | cap |
|---|---|---|---|
| largest mean abs delta between consecutive frames | < 0.002 (motion planned) | static (WARN) | 0.5 |
| largest mean abs delta between consecutive frames | > 0.35 | flicker (WARN) | 0.7 |
| a consecutive pair's mean abs delta | < 1e-4 | duplicate (INFO) | — |
| mean edge density | < 0.002 | near-flat gradient (`low_detail`, WARN) | — |
| pixels under luminance 0.03 | over 97 % in every frame | black (ERROR) | 0.5 |
| pixels over luminance 0.98 | over 97 % in every frame | blown (ERROR) | 0.5 |
| any NaN / Inf pixel | ≥ 1 | nan (ERROR) | 0 |

## Measured deltas from our own runs

`graphics_v1_flash` + `graphics_v2_flash`, 5 graded opengl_python runs, mined 2026-08-25.
The gate fired on exactly one of them - `ogl_hard_voxel_city`, final score 0.342 - but it
fired repeatedly: flicker in three separate rounds, with max frame-to-frame deltas of
0.373, 0.392 and 0.416, each a flicker finding, and each caused by a
discontinuity between two samples 1.5 to 2 seconds apart rather than by per-frame noise.
The same run also produced `edge density 0.0000`, an entirely smooth image. So the gate is
a weak signal at n=5; the judged criteria below are the stronger one.

Judged criterion means for this language over the same 10 rounds: originality 0.330,
visual_richness 0.375, brief_fidelity 0.420, motion_quality 0.510, composition 0.515,
technical_cleanliness 0.605, colour_light 0.610.
