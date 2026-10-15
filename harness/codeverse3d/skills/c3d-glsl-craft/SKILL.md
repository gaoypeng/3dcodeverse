---
name: c3d-glsl-craft
description: "Use when writing, refining or repairing a glsl_shader fragment shader, or when gl_frames reports static, flicker or low detail. What the five sampled frames measure and where the real operating band is, what the shader_v2 judge names and charges for, and a short list of strong optional practices behind its recorded complaints: structure that does not alias, motion from the mechanism instead of a uv scroll, light as a path through a volume, exposure before colour."
license: Apache-2.0
compatibility: GLSL 330 core via moderngl, headless. One image pass (src/shader.frag), an optional src/common.glsl and one feedback pass (src/buffer_a.frag). The harness declares the uniforms; the contract lists them.
metadata:
  evidence: mixed
  evidence_note: "Gate thresholds, caps and rubric weights are read from live code (spatial/frame_stats.py, judges/rubrics/shader_v2.yaml) and pinned in _claims. The corpus is thin: 9 graded glsl_shader runs mined 2026-08-25 under the previous rubric, so every count below names its n and no rate is generalised."
  verified: "2026-10-15"
  corpus: "9 graded glsl_shader runs (18 judged rounds) + 5 opengl_python runs, mined 2026-08-25"
  target_metric: "mean_edge_density"
  target_direction: "up"
  target_unit: "mean edge density over the 5 sampled frames (frame_stats)"
  target_measurable: "true"
  target_baseline: "0.167 median / 0.203 mean edge density; n=9 (bench/out, last gated round, 2026-08-25); range 0.031-0.464"
---

# What separates a graded shader from a demo

**Read this if** you are writing `src/shader.frag`. The contract states the interface and
what the harness measures; this sheet is the layer above it: what the measurement can and
cannot see, and a few strong options that answer the judge's recorded complaints. None of
them is a recipe — the brief decides what is on screen and how it is built.

## The gate is a floor, not the bar

`spatial/frame_stats.py` renders five frames — t = 0, 1, 2.5, 4, 6 s — and measures them:

* `static` — the largest mean |Δ| between consecutive frames is under 0.002 (WARN, score cap 0.5 when motion is planned)
* `flicker` — a consecutive pair differs by more than 0.35 of the image (WARN, cap 0.7)
* `black` / `blown` — in every frame over 97 % of the pixels sit under luminance 0.03, or over 0.98 (ERROR, cap 0.5)
* `low_detail` — almost no pixel sits on a visible luminance edge (WARN)
* `nan` — any NaN / Inf pixel (ERROR, cap 0)

Over the 9 graded glsl runs mined 2026-08-25 the gate fired nothing while the judge sat at
0.55-0.65: clearing it proves nothing. The judge is `shader_v2`: likeness 0.26,
depth_and_detail 0.16, light_and_tone 0.16, motion_quality 0.14, composition 0.10,
technical_cleanliness 0.10, craft 0.08, plus the defect list the system prompt carries.
One hint, and only a hint at n = 9: the harness's colourfulness metric (`gl_frames` prints
it) ranked with the final score at +0.75, edge density at +0.40 and frame-to-frame motion at
+0.20 — the palette is worth more attention than another octave.

## Strong options (none required; each answers a recorded complaint)

**Scales.** One octave at one frequency is both flat (`flat_fill`) and shimmery
(`banding_or_aliasing`): a recorded major issue reads "uniform high-frequency noise rather
than multi-scale fractal relief, causing severe aliasing". Fine octaves smaller than a pixel
alias — fade them with the distance or the footprint the pixel represents.

**Edges.** A `step` aliases; a transition about one pixel wide (from `fwidth` or the
resolution) does not. A staircase edge is the `banding_or_aliasing` defect.

**Banding.** A long two-stop gradient bands on 8-bit output (3 of 6 fancy-battery runs,
2026-08-31). A sub-LSB dither on the final colour — one hash per pixel, added after the
tone-map and gamma — removes it.

**Tone.** Additive light sums (glow, bokeh, scattering) pass 1.0 quickly: one tone-map at
the very end keeps them under the `blown` threshold; gamma applied twice washes the frame
(`white_wash`).

**Exposure first.** Hue and palette work is invisible on dark, low-chroma pixels. Read the
mean luminance `gl_frames` prints and compare it with what the brief's place would show
before grading colour.

**Night subjects.** The judge's `no_dark_range` and `white_wash` defects: a night, space or
dusk frame needs near-black pixels somewhere, and haze and bloom belong on the light, not on
the frame. `gl_frames` prints the mean luminance and the black-pixel fraction — read them.

**Across the flow.** Water, wood, marble, cloth and stone read as a sliding texture when the
pattern is stretched along the direction of movement (the judge's "texture scrolling under a
fixed shape" is 0.5 on motion). Put the fine axis of the pattern across the flow and advect
only the phase.

**Motion from the mechanism.** The frames compared are 1-2 s apart; a single scroll of the
uv by `u_time` gives the same delta everywhere and reads as a conveyor belt. Drive each
element from its own quantity — a drop falls with the square of its time, a flame's tips lag
its base, a spark has a hash-seeded lifetime — and give every instance a phase from its id so
t = 0 is not a symmetric frame.

**Continuity.** `fract` and `mod` of `u_time` are cuts; wrap through a smooth function,
cross-fade the wrap, or hide it under a fade that reaches zero at the wrap. A visible cut
between two sampled times is the `flicker` warning.

**Domain warp.** Feeding fbm with fbm (IQ's domain warping) gives billowing edges to clouds,
smoke and marble; it costs octaves, so budget them.

**Volumes.** A shaft or a lit cloud is scattering integrated along the view ray: march front
to back, attenuate by Beer-Lambert transmittance, weight each step by a phase function
normalised over the sphere (Henyey-Greenstein, +1 forward, in light-travel directions — PBRT's
convention differs) and by a short march toward the light for self-shadow. A radial falloff
around a point has neither direction nor occluder: the sharpest recorded complaint, "a
diffuse, uniform spherical glow rather than directional rays", is that term. A cloud is
brighter where its shell is thin toward the light — sample the density a short step toward
the sun and light the difference.

**Surfaces.** A dielectric (glass, water) is a Fresnel-weighted reflection plus a tinted
transmission; a metallic tint reads as metal, not glass. GGX with Smith visibility and Schlick Fresnel on linear
inputs gives a microfacet response; roughness changes the highlight's shape, a shiny colour
cannot.

**Raymarchers.** A hit tolerance that grows with distance, the same epsilon for the normal,
and a relaxed step. The critical bug of the lowest-scoring run (0.30) — "bright cyan/white
pixels along the edges of all geometry at grazing angles" — was a tangential overshoot
feeding a central-difference normal.

## The three uniforms worth designing around

* `u_noise` — one `texture` fetch is a bilinear-interpolated hash, several times cheaper
  than a hash plus interpolation, so an fbm built on it affords more octaves. Scale the uv,
  not the result.
* `u_prev` — this pass's previous frame: trails, motion blur, accumulation. The contract
  states when it saturates.
* `u_buffer_a` — a whole extra pass. When the effect *is* a simulation (advection,
  reaction-diffusion, a height field), simulate in `buffer_a.frag` and shade in
  `shader.frag`. No graded glsl run in this corpus used it (0 of 9), while all five
  critical effect failures recorded in the sibling opengl_python runs are simulations with
  nowhere to keep their state.

## Before you finish

`gl_probe` compiles and shows frame 0; `gl_frames` renders all five judged times and prints
mean luminance, colourfulness, edge density and the per-pair deltas. Look at the contact
sheet, then read the numbers: a largest delta just above the static threshold with a brief
that promised motion means the motion is there but invisible.
