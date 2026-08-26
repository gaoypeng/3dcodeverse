# The graphics loop — what is judged, what feeds back, and how to keep making it better

*2026-08-26.  Companion to `docs/EVAL.md` (protocol) and `docs/ARCHITECTURE.md` (the track).
Everything here is measured on this harness; numbers are cited, not re-derived.*

The graphics track (`glsl_shader`, `opengl_python`) shares the judge **machinery** with every
other track — `judges/vlm_judge.py` builds the messages, `judges/scoring.py` turns N samples
into one code-computed verdict, `judges/caps.py` applies gate caps and the defect checklist.
What is track-specific is (a) the rubric (`judges/rubrics/shader_*.yaml`: criteria, anchors,
caps, the defect checklist), (b) the judge context the track writes (`tracks/graphics.py::
judge_context`: the harness frame metrics), (c) what the refine session receives
(`prompts/tracks/refine_graphics.j2`), and (d) since today, the reference photos.  Until today
only (b) and (c) had been designed for graphics; the rubric had never been calibrated against a
person looking at the frames.  This document is the loop that does that, and the first turn of it.

## 1. What the loop looks like

```
brief ──plan──▶ shader ──build──▶ frames @ t=0,1,2.5,4,6 ──metrics──▶ gl_frames gate
                                       │                                   │
                                       ▼                                   ▼
                              contact sheet + metrics ──▶ JUDGE (rubric) ──▶ score, issues, plan
                                       ▲                                   │
                                       │        refine_graphics.j2 ◀───────┘
                                       └──── refine session (sees the sheet, the photos, the plan)
```

* **Deterministic first** (law 3): `spatial/frame_metrics.py` measures every sampled frame — mean
  luminance, colourfulness, edge density, |Δ| between frames, black / blown fraction, NaN count —
  and `gl_frames` gates static / flicker / black / NaN.  These are in the judge's context as facts.
* **The judge** sees one labelled contact sheet (frames are the "views"; there are no cameras),
  the metrics digest, the plan digest (passes, key visuals, motion) and the acceptance checklist
  the planner wrote; it answers observe-then-score JSON; code computes the weighted overall,
  applies floors, gate caps and the defect checklist (`scoring.py`).
* **The refine session** gets a prioritised change list (gate failures first, then the judge's
  improvement plan with `instruction` per item), the judge summary, the metrics table, the plan,
  the checklist, the current files — and, since 5688051 / 0aee3b8, the contact sheet the judge
  scored and the reference photos as images.  This part works: the accretion-disc teaser went
  0.24 → 0.75 in one round on exactly this prompt (`bench/out/teaser/runs/tsr_gfx_accretion_disc`).

## 2. What was wrong (measured before the first turn)

Seventeen graphics runs with a judged round (graphics_v1/v2 batteries, the teaser, the codex
wave) were read by a person and scored 0–1 on "would a Shadertoy curator screenshot it / does
it look like the thing".  Against the loop-time judge (`shader_v1`, pro):

| | judge v1 | eye |
|---|---|---|
| mean | 0.773 | 0.550 |
| Spearman(judge, eye) | **0.16** | — |

Three aurora versions that no one would accept as an aurora scored 0.78 / 0.94 / 0.94 with empty
issue lists; a neon-rain of hard opaque pastel discs scored 0.92; a lifted purple nebula wash
0.82 — while a crisp ukiyo-e wave (0.59) and a crafted mandala (0.60) were capped by
planner-written must items.  The rubric rewarded *the nouns of the brief being present* and had
no notion of likeness, tonal range or the artefacts flash actually produces (a comb of evenly
spaced bars for anything organic; hard shapes for light; a white wash for glow).  The refine
loop then faithfully optimised toward that judge.

Eye scores: `scratchpad/gfx_eye.json` → copied to `bench/out/judge_calib_graphics/eye.json`.

## 3. The first turn: `shader_v2` + reference photos + LikenessJudge

* **`judges/rubrics/shader_v2.yaml`** — `brief_fidelity` becomes **`likeness`** (0.26, floor
  0.3): would someone who has seen the real thing accept the frames — dominant colour, where the
  secondary colours sit, how the structure folds / thins, where the light sits, how much stays
  dark, scale relations; "all the nouns, wrong physics" is anchored at 0.4–0.5.
  `visual_richness` → `depth_and_detail`; `colour_light` → `light_and_tone` (darks must exist in
  night/space subjects; no milky wash; no candy saturation); `originality` → `craft` (a
  consistent stylised look is craft).  The instructions calibrate the scale ("median competent
  shader 0.55–0.65; 0.85+ is a screenshot").  New checklist defects name the observed failure
  modes: `comb_artefact` (cap 0.65), `opaque_shapes_for_light`, `flat_fill`, `no_dark_range`,
  `white_wash`, `candy_saturation`, `unlike_reference` (cap 0.6, only with photos attached).
  `missing_must_acceptance` cap 0.7 (was 0.6): planner must items are fallible on this track.
* **Reference photos** — `bench/refs/<prompt_id>/*.png|jpg` (README there).  Attached to the
  Spec by every bench driver; graphics/scene tasks carry them as images on the first message
  with a note that asks for the physics, not the composition (`tracks/prompting.py::
  _likeness_note`); the judge becomes `judges/reference.py::LikenessJudge` — the same photos
  beside the frames, no silhouette IoU (that is an object-track measurement).  Same battery with
  the folder present vs absent is the A/B (`bench/prompts/refs_v1_graphics.yaml`, in flight).
* **Seeded recipes** (`tracks/graphics_recipes.py`, `Settings.limits.seed_recipes` / `CV3D_SEED_RECIPES`,
  default ON) — measured 2026-08-26 on `refs_v2_graphics` (aurora brief, flash api-agent, shader_v2): the
  baseline prompt carried the cookbook's Light chapter with `curtain()` five times and `src/shader.frag` called
  it zero times in both finished runs — round 0 a comb of bars (0.33), a later round a wash (0.12).  Showing
  flash a recipe is not flash using it, so the harness writes the recipes the brief selects (the same
  chapters `select_cookbook_chapters` puts in the prompt, minus the always-on helper chapters and the
  raymarching template) to disk before the session — function definitions only, plus exactly the
  `hash / noise / fbm` helpers they call.  **Measured again 2026-08-26 (`bench/out/seed_v1`, same brief /
  model, seeding ON into `src/common.glsl`)**: `recipes.seeded` fired and the finished run's `common.glsl`
  had NO seeded block and no `curtain(` — the agent overwrote the file with its own helpers — and
  `shader.frag` called neither `aurora(` nor `curtain(` nor `stars(`.  Seeding into a file the agent owns
  is not sticky, so the file is now the **harness-owned `src/recipes.glsl`**: `AgentJob.read_only` makes
  `write_file` / `edit_file` refuse it ("harness-owned — call its functions instead"; `read_file` works),
  `wrap.compose` pastes it harness header < `recipes.glsl` < `common.glsl` < `shader.frag` (a compile error
  inside it is reported as `src/recipes.glsl:LINE`), it is self-contained (its helpers travel with it, so
  nothing the agent does to `common.glsl` breaks a recipe), and a name it defines redefined in an agent file
  is the lint ERROR `redefines_recipe` ("`name` is already provided by src/recipes.glsl — call it instead of
  redefining it").  The only `common.glsl` the harness edits is the untouched skeleton, which loses the
  helpers `recipes.glsl` now provides (else the first build fails on `hash12` twice); once the agent has
  written it, a duplicate is its own to remove.  The baseline prompt names the recipes ("call these, do not
  redefine them, do not copy them into common.glsl"); the refine prompt carries a one-line reminder; a resume
  appends only names `recipes.glsl` does not define.  The aurora brief seeds `skyGrad, sun, stars,
  waterHeight, curtain, auroraCol, aurora` + `hash12, hash22, noise, fbm`; the neon-rain brief `dropsLayer,
  bokehSoft`; a brief matching no chapter seeds nothing, creates no file and the prompt has no block.  The
  event is `recipes.seeded {file, names, present, chapters, trimmed}`; `CV3D_SEED_RECIPES=0` is the control
  arm.  The number to watch stays the `comb_artefact` firing rate — and now `grep -c "aurora(\|curtain(\|stars("
  src/shader.frag` (a call into `recipes.glsl` is the only way those names can appear in a passing build).
* **Measuring the turn** — `bench/judge_calib_graphics.py` re-judges the corpus under v1 and v2
  (pro, n=2, no planner acceptance so the rubric is measured on its own) and prints
  Spearman(judge, eye), means, defect firing rates and the biggest disagreements.  The result of
  the first run is recorded in §5 below and in EVAL.md §6; `TRACK_INFO[graphics].rubric` moves
  to v2 only if the correlation clearly improves.

## 4. How to run the next turn (the recipe)

1. **Look.**  Pick 10–20 judged graphics runs (`rounds/rNN.json` has the sheet path); score
   them by eye into `eye.json`.  Disagreements between eye and judge are the material; write
   down *why* each one is wrong in one line.
2. **Name the failure.**  Every "why" becomes either a checklist defect (binary, with a penalty
   and, when it should dominate, a cap), an anchor sentence in the criterion it belongs to, or a
   deterministic metric if it can be measured (`frame_metrics.py` — e.g. black fraction already
   exists; a "dark range" metric = fraction of pixels < 0.05 luminance would make
   `no_dark_range` a gate instead of an opinion).  Prefer the metric.
3. **Bump the rubric version** (`shader_vN+1.yaml`, never edit the one a battery reports on —
   EVAL.md §6 rule) and run `bench/judge_calib_graphics.py --rubrics shader_vN,shader_vN+1`.
   Keep the turn only if Spearman rises and the mean moves toward the eye mean; look at the
   biggest remaining disagreements before believing it.
4. **Switch** `TRACK_INFO` and re-run one small battery (`graphics_v2_flash`, 10 prompts) so
   the refine loop optimises toward the new judge; compare the sheets, not just the numbers.
5. **Reference photos** for any prompt that names a real phenomenon or place: drop them in
   `bench/refs/<id>/` — the agent and the judge both see them; nothing else changes.
6. Record the turn in this file (§5) and EVAL.md §6 with the numbers.

Things this loop will not fix and should not try to: the judge model's own taste (use n=2 and
pro), planner must items that are wrong (they cap at 0.7 now; the fix is the planner prompt),
and generator capability (a better rubric makes flash's ceiling visible, it does not raise it).

## 5. Turns recorded

| date | rubric | n runs | Spearman(judge, eye) | judge mean | eye mean | note |
|---|---|---|---|---|---|---|
| 2026-08-26 | shader_v1 (loop-time scores) | 17 | 0.16 | 0.773 | 0.550 | before any turn |
| 2026-08-26 | shader_v1 (re-judged, pro n=2, plan digest + frame metrics, no acceptance) | 17 | 0.34 | 0.736 | 0.550 | `bench/out/judge_calib_graphics/summary.md` |
| 2026-08-26 | shader_v2 (same replay) | 17 | **0.71** | 0.562 | 0.550 | same run — the switch is earned |

Reading the v2 table: the three aurora versions land at 0.46–0.55 with `comb_artefact` capping
them (eye 0.30–0.40); neon-rain 0.66 with `opaque_shapes_for_light` (eye 0.45); the ukiyo-e wave
0.61 with no defects (eye 0.80 — still under-scored, but no longer capped); the murmuration 0.875
(eye 0.85).  The two misses are `garbage_pixels` firing on particle dots (the galaxy: 0.30 vs eye
0.70; the reaction-diffusion rings, which are bad anyway) — the checklist text now says particles,
stars and film grain the brief asks for are not garbage; a gate cannot veto this one because the
NaN gate measures NaN, not "garbage".  `no_dark_range` fired on 7/17, which matches what the eye
saw: flash lifts its night scenes.  A first replay without the plan digest and frame metrics gave
v1 0.23 with four moving effects called static — the replay must hand the judge what the loop hands
it (`bench/judge_calib_graphics.py` does now).

**The reference-photo A/B, first turn** (`refs_v1_graphics`: the aurora brief, three seeds with
the two photos and three without, flash, 2 rounds, judged in-loop by v1 / LikenessJudge(v1);
read out offline with ONE LikenessJudge(shader_v2) and the same photos on every run —
`bench/out/refs_v1_graphics/refs_ab_readout.md`): with photos 0.461 / 0.220 / 0.353, without
0.331 / 0.095 / 0.192; paired **+0.139**, sd 0.020, sign 3/3 (n=3 — a direction, not a
number).  Every one of the six runs fired `comb_artefact` and `unlike_reference`: flash draws
evenly spaced bars for an aurora whether or not it is shown one, and the in-loop v1 judge gave
those runs 0.60–0.94.  The photos moved the judge more than the generator — which is why the
second turn changes the generator's material: the cookbook's light chapter (aeb49c0) and
brief-selected chapters (137541e), with shader_v2 in the loop (`refs_v2_graphics`, launched
12:30; read it out with `bench/refs_ab_readout.py bench/out/refs_v2_graphics --refs
bench/refs/tsr_gfx_aurora_ridge`).  The number to watch is the `comb_artefact` firing rate.

**Second turn read out** (`refs_v2_graphics`, 14:00: same six runs, shader_v2 in the loop, the
light chapter in the prompt, the 503 fixes; every run finished 3 rounds in 18–51 min where the
first turn took 60–77 min for 1–2): same-judge scores with photos 0.258 / 0.279 / 0.634, without
0.441 / 0.169 / 0.000; paired +0.187, sd 0.41, sign 2/3.  **All six still fired `comb_artefact`**:
the prompt carried `curtain()` five times and no run called it (`grep -c "curtain(" src/shader.frag`
= 0).  A recipe shown is not a recipe used.  Third turn: `tracks/graphics_recipes.py` seeds the
brief's matched recipes to disk before the session and the prompt says "call them, do not rewrite
them" (`CV3D_SEED_RECIPES`, default ON; A/B `bench/out/seed_v1`, three photo prompts, plan-pinned,
variant = seeding OFF; readout = comb_artefact rate and `curtain(` calls).  **First `seed_v1` read
out** (aurora brief, seeding ON): the seed fired and was gone — the agent rewrote `src/common.glsl`,
seeded block included, and called none of `aurora( / curtain( / stars(`.  A recipe the agent can
overwrite is not a recipe kept: the seed now lives in the harness-owned, read-only `src/recipes.glsl`
(§3 "Seeded recipes"); the same A/B re-runs against it.

**The track default is `shader_v2`** since c032700 (the switch waited for the first reference
A/B to finish so both of its arms were judged in-loop by one rubric).
