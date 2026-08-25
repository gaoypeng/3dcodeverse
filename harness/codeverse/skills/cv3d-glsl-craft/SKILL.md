---
name: cv3d-glsl-craft
description: Craft rules for the fragment shaders this harness judges — what the five sampled frames measure and where the real operating band is, how to build multi-scale structure that does not alias, how to make motion come from the mechanism instead of a uv scroll, and why an added radial falloff reads as a uniform glow instead of light shafts. Use in any graphics session with language glsl_shader when writing, refining, repairing or rebuilding the shader, and whenever gl_frames reports static, flicker or low detail.
license: Apache-2.0
compatibility: GLSL 330 core via moderngl, headless. One image pass (src/shader.frag), an optional src/common.glsl and one feedback pass (src/buffer_a.frag). The harness declares the uniforms; the contract lists them.
metadata:
  evidence: mixed
  evidence_note: "Gate thresholds and rubric weights are read from live code (spatial/frame_stats.py, judges/rubrics/shader_v1.yaml). The corpus is thin: 9 graded glsl_shader runs, so every count below names its n and no rate is generalised."
  verified: "2026-08-25"
  corpus: "9 graded glsl_shader runs (18 judged rounds) + 5 opengl_python runs, mined 2026-08-25"
  target_metric: "mean_edge_density"
  target_direction: "up"
  target_unit: "mean edge density over the 5 sampled frames (frame_stats)"
  target_measurable: "true"
  target_baseline: "0.167 median / 0.203 mean edge density; n=9 (bench/out, last gated round, 2026-08-25); range 0.031-0.464"
---

# What separates a graded shader from a demo

**Read this if** you are writing `src/shader.frag`. The contract already tells you which
uniforms exist and how to keep the maths NaN-free. This is the layer above that: what the
measurement can and cannot see, and the four craft rules that the judge's complaints in this
corpus actually name.

## The gate is a floor, not the bar

`spatial/frame_stats.py` renders five frames — t = 0, 1, 2.5, 4, 6 s — and measures them:

| what | constant | fires at |
|---|---|---|
| static | `STATIC_DIFF` | mean absolute pixel difference between consecutive frames **< 0.002** |
| flicker | `FLICKER_DIFF` | any consecutive pair **> 0.35** |
| duplicate frames | `DUPLICATE_DIFF` | a pair under 0.0001 (INFO) |
| near-flat image | `LOW_DETAIL_EDGE` | mean edge density **< 0.002** (fraction of pixels whose luminance gradient exceeds 0.02) |
| black / blown | `BLACK_LUM` 0.03, `BLOWN_LUM` 0.98 | > 97% of pixels in every frame; ERROR, and the gate hint asks for mean luminance 0.2-0.6 |

Measured over the 9 graded glsl runs (mined 2026-08-25), **the gate fired zero findings**:
mean frame-to-frame difference ranged 0.013 to 0.182 (median 0.082) and edge density 0.031
to 0.464 (median 0.186). The three flicker warnings and one low-detail warning in the whole
graphics corpus came from `opengl_python`, not from shaders. So clearing the gate proves
nothing. What is actually low is the judge: over 18 judged rounds the weakest criteria are
`technical_cleanliness` 0.597, `originality` 0.600, `colour_light` 0.606 and
`visual_richness` 0.619 — and `visual_richness` carries the largest weight of the four
(0.18) in `shader_v1`.

One more measured hint, and it is only a hint at this n: across those 9 runs the rank
correlation between the harness's **colourfulness** metric and the final score is **+0.75**,
while edge density manages only +0.40 and frame-to-frame motion +0.20. With n = 9 that is
not a law, but it says the palette is worth more attention than another fbm octave. The
best-scoring run (0.92) is also the most colourful and the most detailed; the worst (0.30)
is the flattest and greyest of the nine.

The four rules below are aimed at exactly those four criteria.

## 1. Structure at several scales, or it aliases

`visual_richness` asks for "structure at several scales", and `technical_cleanliness`
penalises aliasing and banding. In this corpus those are the *same* mistake seen twice — a
recorded major issue reads: *the wall displacement is uniform high-frequency noise rather
than multi-scale fractal relief, causing severe aliasing.* One octave at one frequency is
both flat (no scales) and shimmery (nothing filters it).

* Build relief as fbm and let the amplitude fall with the distance the pixel represents:
  drop octaves as `1/(1 + k*t)` where `t` is the ray distance or the screen-space scale.
* Anti-alias every edge with a width, not a step: `smoothstep(-w, w, d)` with
  `w = 1.5 / u_resolution.y`, scaled by the local derivative if the feature is in
  perspective.
* Raymarchers: grow the hit tolerance with distance (`eps = max(4e-4, 1.5e-3 * t)`) and take
  the normal with the same epsilon. The lowest-scoring run of the nine (0.30) was graded with
  a *critical* bug for "bright cyan/white pixels along the edges of all geometry at grazing
  angles" — that is a tangential overshoot feeding a central-difference normal.
* Grade with a palette function, not a `mix` of two constants, and dither the last step
  (`col += (hash12(fragCoord) - 0.5) / 255.0`) — banding in a large gradient is a named
  `technical_cleanliness` deduction and one line removes it.

## 2. Structure runs across the flow, not along it

Water, wood, marble, cloth and stone all read wrong when the pattern is stretched *along*
the direction of movement, because that is what a scrolling texture looks like. Warp the
domain perpendicular to the flow and advect only the phase:

```glsl
vec2 flow = normalize(vec2(1.0, 0.35));
vec2 across = vec2(-flow.y, flow.x);
float band = fbm(p * vec2(2.0, 9.0) * mat2(across, flow) - flow * u_time * 0.6);
```

The high-frequency axis is the one across the flow. This is the difference between "a
surface moving" and "a texture sliding", and it costs nothing.

## 3. Motion comes from the mechanism

`motion_quality` grades "the way the brief says … continuous, paced and plausible", and the
frames it compares are 1 to 2 seconds apart. A single `uv + u_time * k` scroll gives the
same delta everywhere and reads as a conveyor belt.

* Drive each element from its own quantity: a drop falls with `t*t`, a flame's tips lag its
  base, a pendulum is `sin`, a spark is a hash-seeded lifetime.
* Give every instance a phase from its id, `hash12(id) * 6.2831`, or the whole field pulses
  in lockstep and t = 0 is a suspiciously symmetric frame.
* Keep it continuous. `fract()` and `mod()` of `u_time` are cuts; wrap through a smooth
  function or cross-fade the wrap. A cut between two sampled times is the `flicker` warning
  and a `technical_cleanliness` deduction.
* Nothing may depend on `u_frame` alone: the sampled times are not evenly spaced.

## 4. Light is a path through a volume, not a falloff around a point

The sharpest recorded effect complaint in this corpus is: *distinct volumetric light shafts
are missing; the lighting appears as a diffuse, uniform spherical glow rather than
directional rays.* That is exactly what `col += A * exp(-k * dist)` produces — a radially
symmetric blob, because the term has no direction and no occluder in it.

A shaft is the integral of scattering along the view ray, so march the ray and, at each
step, weight by the phase angle to the light and by whether that point is shadowed:

```glsl
float shaft = 0.0, t = 0.0;
for (int i = 0; i < 48; i++) {
    vec3 pos = ro + rd * t;
    float vis = step(0.0, sdScene(pos + lightDir * 0.35));   // cheap occlusion probe
    float phase = 0.6 + 0.4 * pow(max(dot(rd, lightDir), 0.0), 8.0);
    shaft += vis * phase * exp(-0.08 * t);
    t += 0.12;
}
col += lightCol * shaft * 0.02;
```

The same principle covers the other "reads as the thing" cases: a lit cloud is brighter
where the shell is *thin toward the light* (sample the density a short step toward the sun
and take the difference), and a glass or water surface is a dielectric — a Fresnel-weighted
reflection plus a tinted transmission, never a metallic tint.

## The three uniforms worth designing around

The contract declares them; what they are *for* is the craft:

* `u_noise` — a 256x256 repeating RGBA noise texture with linear filtering. One `texture()`
  fetch replaces a hash and interpolation, so an fbm built on it is several times cheaper
  and lets you afford more octaves. Scale the uv, do not scale the result.
* `u_prev` — this pass's previous frame. Trails, motion blur and accumulation, always with
  a decay under 1.0 and preferably a sub-pixel drift, or it saturates by t = 6 s.
* `u_buffer_a` — a whole extra pass. Use it when the effect *is* a simulation (advection,
  reaction-diffusion, a height field): simulate in `buffer_a.frag`, shade in `shader.frag`.
  **No graded glsl run in this corpus used it (0 of 9)**, while **all five** critical effect
  failures recorded in the sibling opengl_python runs are simulations with nowhere to keep
  their state.

## Before you finish

`gl_probe` compiles and shows frame 0; `gl_frames` renders all five judged times and prints
mean luminance, colourfulness, edge density and the per-pair deltas. Look at the contact
sheet, then read the numbers: a mean delta near the bottom of the 0.013-0.182 band with a
brief that promised motion means the motion is there but invisible.

Worked snippets — fbm with distance-faded octaves, the flow-aligned warp, hash-phased
instances and the dither — are in `references/shader-recipes.md`.
