# Evaluation protocol

How we decide whether the harness (plan → generate → gate → render → judge →
refine) beats raw generation, and whether one backend beats another, without
fooling ourselves.  Tools: `3dcv bench`, `bench/compare_backends.py`,
`codeverse.judges.{vlm_judge, pairwise, calibration, metrics}`.

## 1. Principles

1. **Fixed judge.**  One judge model + rubric + `n_samples` for every arm of a
   comparison, never the in-loop judge's own score.  Default for decisive runs:
   `gemini:gemini-3.1-pro-preview` (the settings default), `n_samples≥2`;
   `gemini-3.7-flash` is fine for the loop but is lenient/noisy on fine
   distinctions (overall std ≈ 0.08–0.12 at n=3) — its dynamic range comes from
   the rubric defect checklist, not the criteria.
2. **Same evidence for every arm.**  The evaluator rebuilds from code: lint+build →
   `measure_glb` → connectivity gate → canonical 8-view `render_glb` (`OBJECT_VIEWS`,
   studio rig) → `VlmJudge`.  A failed build scores 0 (error recorded).  Acceptance
   items come from the battery's `must_have` list for *all* arms (no plan-derived
   items, so harness and one-shot are judged alike).
3. **Scores are code-computed.**  Weighted criteria, floors, caps from gate findings,
   `passed = overall ≥ threshold ∧ no floor ∧ all must-acceptance`; the ledger is in
   `Judgment.raw` (ScoreBreakdown).  Degraded verdicts (`is_degraded`) are re-run, not
   counted.
4. **Stratified batteries.**  Prompts are fixed YAML with ids, tiers (easy/medium/hard)
   and categories; report per tier, never only the mean.
5. **Pairwise for preferences.**  Absolute scores are coarse; `PairwiseJudge` runs
   both orderings and returns `tie` on disagreement; report win/tie/loss counts with
   confidence.
6. **Cost and time are part of the result.**  Every cell records `Usage` (USD, tokens)
   and wall time; compare at equal budget where possible.
7. **Reproducibility.**  `record.prompt_hashes` (contract/cookbook/generate/refine),
   rubric content hash (`ScoreBreakdown.rubric_hash`), `record.environment` (python,
   codeverse, blender, node, three, host) are stored per run; keep seeds fixed
   (`--seed`).

## 2. Batteries (`bench/prompts/`)

| file | track / language | n | strata |
|---|---|---|---|
| `static_objects_v1.yaml` | static_object / blender | 24 | 8 categories × easy/medium/hard |
| `articulated_v1.yaml` | articulated_object / urdf_blender | 12 | tiers |
| `scenes_v1.yaml` | scene / scene_threejs | 12 | tiers |
| `compare_v1.yaml` | static_object / blender | 8 | 2 easy / 3 medium / 3 hard; for harness-vs-one-shot |
| `compare_v2.yaml` | static_object / blender | 8 | 2 medium controls / 6 hard (one per v2 difficulty axis); harness-vs-one-shot on hard prompts |
| `compare_v3.yaml` | static_object / blender | 12 | compare_v2's 8 verbatim + 3 medium controls / 9 hard total (adds tools, vehicles, animals, props); codex-tier battery |

Each prompt: `{id, tier, category, prompt, must_have[], dimensions_m?}`.  `must_have`
becomes acceptance items (planner-appended in the loop, fixed-judge checklist in
comparisons); `dimensions_m` becomes `Spec.constraints.dimensions_m` and a
deterministic contract check.

## 3. Harness battery runs (`3dcv bench run`)

```bash
3dcv bench run bench/prompts/static_objects_v1.yaml \
    --generator gemini-cli:gemini-3.7-flash --judge gemini:gemini-3.1-pro-preview \
    --rounds 2 --parallel 4 --out bench/out/static_v1_apiagent
3dcv bench run bench/prompts/static_objects_v1.yaml --generator gemini-cli:gemini-3.7-flash --judge gemini:gemini-3.1-pro-preview --out bench/out/static_v1_gemcli
3dcv bench report bench/out/static_v1_apiagent
```
Each item = one full `3dcv make` run (its own workspace under `--out`); `results.jsonl`
rows carry `score_baseline`, `score_final`, `passed`, `rounds`, `cost_usd`, `minutes`,
`status`, `generator`, `judge`.  `report.md/html` tabulates per tier and category:
mean baseline → final (the harness delta), pass rate, cost, time.  Keep `--judge` fixed
across arms; vary only `--generator` (and `--planner` if that is the variable).
Resumable: re-running the same `--out` skips finished ids.

Reading the numbers: *baseline* (round 0) is the raw-model-with-contract result;
*final − baseline* is what the refine loop buys; the pass rate at the rubric threshold
(0.72 for `static_object_v1`) is the headline.  Observed so far (flash judge,
n=1, single runs): chair 0.67 → 0.74, bench 0.64 → 0.89, cabinet 0.68 → 0.93,
scene 0.56 → 0.58 after one refine.

## 4. Harness vs raw one-shot (`bench/compare_backends.py`)

Answers "does the harness beat a strong model writing the file in one go?" —
including `claude-code` / `codex` one-shot, which cannot run inside the loop as
cheaply.

```bash
python bench/compare_backends.py --prompts bench/prompts/compare_v1.yaml \
    --arms harness:gemini-cli:gemini-3.6-flash,harness:gemini-cli:gemini-3.7-flash,oneshot:claude-code,oneshot:codex,oneshot:gemini:gemini-3.7-flash,oneshot+repair:gemini:gemini-3.7-flash \
    --judge gemini:gemini-3.1-pro-preview --out bench/out/compare_v1 [--parallel 3] [--limit N] [--ids a,b]
    [--rounds 3] [--loop-judge gemini:gemini-3.7-flash] [--repair-attempts 2] [--gen-timeout 900]
    [--no-pairwise] [--no-resume] [--report-only]
```
Arms:
* `harness:<generator-id>` — the full static_object track (rounds ≤ `--rounds`);
  its in-loop judge is `--loop-judge` (default: settings default); the loop's own score
  is **not** the reported score.
* `oneshot:<x>` — ONE raw generation from prompt + minimal contract (no tools, cookbook,
  plan, repair); `x` ∈ `claude-code[:model]` · `codex[:model]` · `gemini|anthropic|openai:<model>`.
* `oneshot+repair:<x>` — same plus ≤ `--repair-attempts` build-error-feedback retries
  (labelled; never judge feedback).

Every arm ends with a `src/model.py` copied into a fresh eval workspace and scored by
the same `FixedEvaluator` (`BlenderRuntime` lint+build → measure → connectivity →
8-view render → `VlmJudge(static_object_v1, --judge, n_samples=2)`).  Then a pairwise
arena runs every harness arm against every one-shot arm per prompt (both orderings).
Output under `--out`: `matrix.json`, `results.jsonl` (cells, resume source),
`pairwise.jsonl`, `cells/<prompt>/<arm>/{run,gen,eval}`, `report.md`, `report.html`.

Report per arm: mean/median score, pass rate, build-failure rate, per-tier breakdown,
cost and wall time per prompt; arena: wins/ties/losses with mean confidence.  Claim a
harness advantage only when (a) the fixed-judge mean and pass rate are higher **and**
(b) the pairwise arena agrees, at comparable or lower cost.

Subscription-backed arms (`claude-code`, `codex`) bill no dollars: their USD is a
notional API-rate estimate from the tokens the CLI reports, so report tokens and
wall-clock alongside it and never compare it to an API arm's real spend without
saying so.  Run them sparingly.  `codex` arms always state their reasoning effort
(`-c model_reasoning_effort=`, default `high` from `Settings.agents.codex_reasoning_effort`,
per-arm override `codex:<model>@<effort>`) — the CLI's own default is *medium*, so an
unstated effort silently changes what a codex arm measures.

### Codex tiers — results (compare_v3, verified 2026-08-24)

**Incomplete: `harness:codex:gpt-5.6-sol` has 2 evaluated cells, `harness:gemini-cli:gemini-3.6-flash` 2, `oneshot:gemini:gemini-3.7-flash` 7; harness terra / luna have none** — flash (the planner) answered on 0/6 keys and pro (the judge) intermittently for the whole window, so only the 8 compare_v2 prompts are covered and none of compare_v3's 4 new prompts has run.  Full table, per-prompt grid and re-pricing notes: `bench/out/codex_tiers_v3/report.md`.

Fixed judge `gemini:gemini-3.1-pro-preview` for every cell; `infra_failed` / outage-text cells excluded (`dropped`), not scored 0; codex USD re-priced from recorded tokens with `codeverse.models.pricing` (terra/luna price rows post-date the runs, so `results.jsonl` shows 0.00 for terra).  All 44 codex invocations (24 one-shot argv, 20 harness trajectory argv) carry `--model gpt-5.6-<tier>` and `-c model_reasoning_effort=high`; the un-tiered `oneshot:codex` arm resolves to sol@high via `~/.codex/config.toml`.

| arm | n | dropped | mean | median | pass | build ok | $gen/run real | $gen/run notional | $/pass |
|---|---|---|---|---|---|---|---|---|---|
| harness:codex:gpt-5.6-sol | 2 | 0 | 0.928 | 0.928 | 2/2 | 100% | 0.26 | 6.97 | 7.23 |
| oneshot:codex (config = sol@high) | 8 | 0 | 0.637 | 0.600 | 1/8 | 100% | 0 | 0.33 | 2.60 |
| oneshot:codex:gpt-5.6-sol | 8 | 0 | 0.627 | 0.600 | 1/8 | 100% | 0 | 0.34 | 2.74 |
| harness:gemini-cli:gemini-3.6-flash | 2 | 0 | 0.600 | 0.600 | 0/2 | 100% | 0.64 | 0 | — |
| oneshot:codex:gpt-5.6-terra | 8 | 0 | 0.481 | 0.551 | 2/8 | 88% | 0 | 0.14 | 0.55 |
| oneshot:claude-code | 8 | 0 | 0.472 | 0.600 | 0/8 | 75% | 0 | 1.17 | — |
| oneshot:codex:gpt-5.6-luna | 8 | 0 | 0.344 | 0.324 | 1/8 | 75% | 0 | 0.01 | 0.11 |
| oneshot:gemini:gemini-3.7-flash | 7 | 1 | 0.146 | 0.000 | 0/7 | 57% | 0.04 | 0 | — |

Judge adds ~$0.10–0.12 real per cell to every arm.  What can and cannot be said:

* **Harness lift** (paired, same prompt): sol **+0.34 on n=2** (medium prompts only, both harness cells pass); flash **+0.60 on n=2** but against one-shot zeros and with the harness stopping at 0.60 on `budget`; terra/luna not measured.  Not a result at n=2.
* **One-shot tier ranking**, n=8: sol 0.627 > terra 0.481 > luna 0.344, and it survives dropping each arm's best and worst prompt (0.609 > 0.491 > 0.302).  It does not hold on pass rate (terra 2/8 vs sol 1/8, luna 1/8).
* **$ per passing artifact**: notional (subscription) luna 0.11 < terra 0.55 < sol 2.7 one-shot; harness sol 7.23 (0.26 real + 6.97 notional).  Real spend on the API arms bought no pass (flash harness $1.27, flash one-shot $0.30).  One or two passes per arm — prices of single artifacts, not rates.

Pending: rerun the parked sol battery, then terra, then luna, then the claude-code / gemini one-shot baselines on the 4 new prompts (one process, `CV3D_MAX_IN_FLIGHT=16`, `--wait-for-provider 240`, `--redo-status infra_failed`).

## 5. Comparing backends inside the harness

Same battery, same `--judge`, different `--generator` (`api-agent:*`, `single-shot:*`,
`gemini-cli:*`, `claude-code:*`, `codex:*`, `agy:*`).  Report baseline, final, delta,
pass rate, cost, time per tier; optionally feed the best rounds of two arms through
`PairwiseJudge.compare(spec, renders_a, renders_b)` (`flywheel pairs` already emits
cross-backend candidate pairs keyed by prompt hash).

## 6. Judge calibration

`codeverse.judges.calibration` re-judges recorded rounds without touching the runs:

```bash
python -m codeverse.judges.calibration <run-dir> [<run-dir> ...] \
    --model gemini:gemini-3.1-pro-preview --n 3 --out out/calib [--geometry clay|normals|none] [--rounds 0,1]
# run dirs: any recorded run, e.g. bench/out/<battery>/runs/<slug>
```
Output: `calibration_<model>.md/.json` with per-round mean±std (capped and uncapped),
which caps/defects fired, pearson/spearman(gate errors vs score), correlation with the
stored in-run scores, per-criterion std and total cost.

**Measured 2026-08-23** (montage prompt + defect checklist + observe-then-score
schema, n=3, e2e chair/cabinet rounds):

| judge | mean overall std | per-criterion std | pearson(gate errors, score) | cost |
|---|---|---|---|---|
| `gemini-3.7-flash` | 0.083 | 0.086 | −0.33 | $0.15 |
| `gemini-3.1-pro-preview` | 0.030 | 0.057 | +0.63 (cap-driven; the rounds are visually near-identical) | $0.80 |

Dynamic range (static rubric, chair brief, n=2): crafted chair / crude drawing /
wrong-object primitive → flash 0.91 / 0.00 / 0.20, pro 0.60 / 0.00 / 0.25 — the
binary defect checklist gives flash the range its raw criteria lack.  Hence the
default judge is pro; flash needs `n_samples ≥ 2` for decisions.
Notes: the earlier criteria-first schema compressed flash to 0.6–0.7 (std 0.01);
"images beat gate text for visible facts" in the prompt fixed pro hallucinating
"nothing moves" from contract-gate text.

* Variance on your own runs: `3dcv judge <slug> --n k` and read `score_std` /
  `judges.metrics.judge_agreement`.
* Sanity anchors: a skeleton placeholder should score ≈ 0.3–0.5; a deliberately wrong
  object should trip the `intent_fidelity` floor / `wrong_object` defect; a floating
  part should cap via `connectivity` findings with `data["kind"]="floating"`.
* A better recurring smoke than re-judging e2e rounds: a separation set with
  deliberately broken variants (exploded / floating / primitive-only).
* **A checklist defect a passed gate measured absent does not cap (7a9b6d3).**  The
  judge's binary checklist feeds a per-item penalty and, for `floating_part` /
  `interpenetration`-class items, a hard cap (`defect:<id>` in `caps_applied`).  Those are
  also what the connectivity gate *measures*.  Measured 2026-08-26 on a plan-pinned pair
  (fancy_v1 `gas_street_lamp`, two lamps the eye cannot tell apart): the gate reported
  "all 9 parts connected, gap ≤ 2 mm" and was in the judge's input; both pro samples read
  the dark seam under the pedestal as "floating in mid-air, a clear daylight gap", and
  `defect:floating_part` capped the run at 0.600 (uncapped 0.720) against 0.962 for its
  sibling.  `judges/caps.measured_absent`: when a `when=gate` cap rule with the same id
  has all its watched gates passed with no ERROR finding of its `kinds`, the checklist
  claim is switched off before the penalty and before `apply_caps`, and named in the verdict
  tail ("checklist claims contradicted by a passed gate").  A failed gate, a gate that did
  not run, or a defect nothing measures (`wrong_object`, `missing_named_part`) are
  untouched.  Re-aggregating the eleven judged fancy_v1 cells from their stored samples
  changed exactly one score (that lamp, 0.600 → 0.720); the other 0.600s are
  `missing_must_acceptance` / interpenetration caps the gates agree with.  0b6f52b tells the
  judge the same thing in its prompt; this holds when the judge does not listen.
* Never tune rubric text against the battery you report on; bump the rubric version
  (`*_v2`) instead and re-run.

### 6.1 The graphics judge, calibrated against an eye (2026-08-26)

The graphics track shared the judge machinery and none of its calibration.  Seventeen judged
graphics runs (graphics_v1/v2 batteries, teaser, codex wave) were scored by a person from the
same contact sheets ("would a curator screenshot it / does it look like the thing"):
**Spearman(shader_v1 loop-time verdict, eye) = 0.16**, judge mean 0.773 vs eye mean 0.550.
Three aurora versions nobody would take for an aurora scored 0.78 / 0.94 / 0.94 with empty issue
lists; opaque pastel discs for bokeh 0.92; a lifted purple wash for a nebula 0.82; a crisp
ukiyo-e wave 0.59 under planner must items.  The rubric scored the nouns of the brief being
present.  `docs/GRAPHICS_LOOP.md` is the loop that fixes this (rubric `shader_v2`: likeness,
tonal range, an artefact checklist; reference photos via `bench/refs/<id>/`; `LikenessJudge`)
and the ledger of turns; `bench/judge_calib_graphics.py` re-judges the corpus under two rubrics
against the eye file and is the gate for switching the track default.  The rule from §6 holds:
never tune `shader_v1` in place — bump the version and re-judge.

## 7. Failures that are not results

A cell can end without a score for reasons that say nothing about the model, and
counting those as zeros silently rigs a comparison.  `bench/_infra.py` classifies
them, and `compare_backends` applies the SAME rule to every arm:

| status | what happened | score | build rate | wall clock |
|---|---|---|---|---|
| `infra_failed` | provider outage — 503/529 storm, read timeout, exhausted key pool | excluded | excluded | excluded |
| `budget_exhausted` | ran out of minutes/dollars with zero rounds and no artifact | excluded | **counts as a miss** | excluded |
| `no_code` | the model answered, but with prose or unparseable code | **0.0** | counts | counts |
| `build_failed` | the code ran and the build failed | **0.0** | counts | counts |

The distinction is not academic.  Before 2026-08-24 the two failure paths in
`run_cell` disagreed: a one-shot cell that got no answer recorded `score=0.0` while a
harness cell killed by the *same* 503 recorded `score=None`.  During a multi-hour
`gemini-3.7-flash` capacity storm this put five hard zeros on the one-shot arm of
`compare_v2_full` and quietly dropped four harness cells — a bias worth roughly a
quarter of a point, pointing the same way as the claim being tested.  `compare_v1_full`
and `compare_v1_live2` were audited and are unaffected.

Rules that follow from it:

* an outage cell is **re-run, never reported** — `--redo-status infra_failed`; the run
  summary prints the exact command and the arm table carries a `dropped` column, so a
  loss can never be mistaken for a result;
* never compare arms measured in different weather.  If one arm ran during a storm and
  another did not, re-run the affected cells before putting the two in one table;
* `budget_exhausted` is deliberately *not* excused on the build rate: the provider is
  not at fault for a model that cannot finish inside the cap.

## 8. The noise floor of a paired A/B (`bench/ab_plan.py`)

`bench/ab_plan.py` runs one switch as control-vs-variant, paired per prompt, both arms
launched together so the weather matches, both scored by the same fixed judge.  The
verdict rule is blunt on purpose (`bench/_ab_report.verdict_of`, stated once):

> keep iff mean paired delta ≥ +0.02 **and** no prompt at ≤ −0.03; revert iff mean ≤ −0.02
> **or** ≥ 2 such prompts; else inconclusive.  A prompt counts only when BOTH arms scored.

**That rule is a screen, not a proof, and the numbers say by how much.**  Measured
2026-08-24 with two A/A runs — arms identical by construction — on the *same* prompt
`ctrl_med_dining_chair` at rounds 1, `gemini-cli:gemini-3.6-flash`, fixed judge
`gemini-3.1-pro-preview` n=2:

| run | control | variant | delta | verdict the rule printed |
|---|---|---|---|---|
| `bench/out/ab_smoke_features` | 0.591 | 0.934 | **+0.344** | **keep** |
| `bench/out/ab_aa_noise` | 0.700 | 0.600 | **−0.100** | **revert** |

Nothing was under test either time, and the rule returned opposite decisions.  Those four
same-code measurements of one prompt have **sd 0.160** (mean 0.706, range 0.591–0.934), so
a paired delta has sd ≈ 0.226 and an 8-prompt mean carries a 2 SE band of **±0.16** —
eight times the ±0.02 the decision turns on.  Resolving ±0.02 at this spread would take
roughly **500 paired prompts**.

The reason is structural, not a bug: a paired delta is the difference of two *independent
stochastic generations*, so it carries generation spread, not the fixed judge's ±0.02
sampling noise.  The ±0.02 threshold was sized against the judge and is roughly an order
of magnitude too tight for what it is applied to.  Consequences:

* every summary now prints a **Confidence** block — paired sd, SE, the 2 SE band, and
  `separated from noise: yes|NO` — beside the verdict, plus `n_for_power`, the number of
  paired prompts this spread would need before ±0.02 is resolvable.  At the spread above
  that is *hundreds*, not eight;
* run `--aa` on the same battery and the same n to measure the floor before believing a
  win.  Both arms get the control environment; the report is titled `A/A` and banners
  itself so no reader can mistake a calibration for a result;
* a `keep` that is not `separated` means "worth another look", never "ship it".  Prefer
  changes whose per-prompt deltas are *consistent in sign* over ones with a big mean and
  a big spread — the sign pattern survives this noise where the mean does not (a sign test
  over 8 prompts needs 7/8 in one direction for p < 0.07, and that is a bar an 8-prompt
  battery can actually clear);
* the cheapest real power is not more prompts but **less per-cell variance**: more rounds,
  or k generations per (prompt, arm) averaged before differencing, cuts the paired sd by
  √k.  Both cost the same dollars as more prompts and buy more per dollar here.

### 8.1 Where the variance actually is: the planner, not the judge

The 8-prompt A/A (`bench/out/plan_loop/C0`, 2026-08-25) put paired sd at **0.202**, SE 0.082,
2 SE band ±0.165, and printed its own conclusion: *~408 paired prompts to resolve +0.02*.
Opening the two arms of its worst pair says why, and it is not the judge:

| `mech_hard_pitcher_pump` | planned parts | rounds | status | built parts | triangles | score |
|---|---|---|---|---|---|---|
| control | **1** — `WoodenPlatform` | 1 | passed | 3 | 4,796 | **0.750** |
| variant | **10** — body, spout, domed cap, clevis, handle, linkage, piston rod … | 2 | budget | 13 | 61,340 | 0.600 |

Identical arms, identical settings.  The planner returned a one-part plan for a pitcher pump
in one arm and a proper ten-part plan in the other, and the 12.8× difference in delivered
geometry is what the judge then scored.  **More judge samples cannot shrink this**; the two
arms were not two measurements of one artifact, they were two different artifacts.

Two consequences, and the first is worth more than any extra prompt:

* **Pin the plan when the change is generation-side.**  Plan once per prompt, write that
  `plan.json` into BOTH arms, and let the arms differ only in what is under test.  That
  removes the dominant variance term outright rather than averaging it down, and costs one
  planner call *less* per pair instead of k times more.  It is only valid when the switch
  cannot affect planning — for a plan-side change (a budget, a fit check, a brief) the plan
  must stay free and the sd above is the price.
* **A degenerate plan is not a rare curiosity.**  7 of 122 recorded static-object runs shipped
  a plan with ≤ 1 part, all after the plan-budget gate landed.  The gate is not broken —
  `plan_quality_complaint` fires on exactly that plan ("Only 1 parts for a request that needs
  about 10") and emits a `plan.thin` event — but after `MAX_QUALITY_REASKS` the planner accepts
  whatever came back, and **nothing downstream is told**.  The pitcher-pump run above carries
  `status: passed`, `score: 0.75`, and no record of the complaint.  The harness formed the
  verdict "this plan is not worth building" and then discarded it; a known-thin plan should
  reach the record and the rubric the way `missing_must_acceptance` does, so it cannot quietly
  out-score a plan that did the work.  (Not a licence to fail the run — that was C1, reverted
  the same day for turning recoverable planning slips into total losses.)

### 8.2 `--pin-plan`: the driver actually does it now

8.1 said to pin the plan; `bench/pin_plan.py` had the seeding and nothing called it.
`bench/ab_plan.py --pin-plan` closes that: before a pair is launched, `pin_pair` plans the
prompt ONCE into `<out>/plans/<id>/run` and seeds that result into both arms' `run/`
workspaces, so the plan stage is a cache HIT (`StageRunner.stage` keys on
`inputs_hash` + result file) and neither arm pays a planner call.

Three details are the whole correctness of it:

* **Both arms are seeded from a third workspace**, not the variant from the control's
  finished run.  Seeding off the control would serialise the pair, and the pair is launched
  together precisely so both arms see the same provider weather.
* **The `inputs_hash` is asserted equal for every arm.**  It is derived from the spec
  (`plan_stage_inputs`), so a mismatch means the seed is a cache MISS and the pair would
  re-plan per arm while reporting itself pinned — the one failure this must not have.
* **`pin_plan_blockers` gates the flag** and `main` refuses the run when it is non-empty.
  Pinning a plan-side switch deletes the thing under test and the rig would then report
  "no effect" with confidence.  `CV3D_SKILLS*` are in `GENERATION_SIDE_ENV`, so the skills
  wave is pinnable; `CV3D_PLAN_BRIEF` is not, and `--pin-plan` rejects it.

Pinning is orthogonal to `--aa`, and an A/A that will be read against a pinned A/B must be
pinned too — otherwise the floor carries a variance term the A/B has already removed and
every delta looks smaller than its own noise.

### 8.3 A metric with no headroom is not an underpowered A/B

`bench/skill_targets.py` prints `n to resolve 25%` for each bundle's own target, and for a
count that is mostly zero the number is not a hurdle, it is a refusal.  Measured over the
recorded corpus, before spending anything:

| target | baseline | paired sd (est.) | pairs to resolve a 25% move |
|---|---|---|---|
| `cv3d-glsl-craft` / mean_edge_density | 0.234, spread 0.031–0.464 | 0.207 unpaired | ~50 unpaired — pinning + pairing is what makes it affordable |
| `cv3d-urdf-joints` / joint_sweep_errors | 6.91 mean, 20 of 23 runs at **0**, tail 8/50/101 | ~32.6 | **~1420** (~89 even to see the metric go to zero) |
| `cv3d-scene-composition` / camera_placement_findings | 0.25 on round 1, **0.00** by the last round | ~0.71 | **~512** (~32 to eliminate every fault) |

Two of those three are answered by arithmetic, not by a battery.  Running them anyway and
reporting "no effect" would be the rig lying about what it can see.

### 8.4 What `ab_plan` can and cannot evaluate — and why the primary readout survives it

Running the rig on a non-blender battery for the first time found two layers of the same
assumption, one fixed here and one only documented:

1. **Fixed.** `compare_backends._run_harness` gated the harness arm on
   `bench/_oneshot.MODEL_FILE` (`src/model.py`).  A glsl_shader control that finished
   `status: passed` having written `src/shader.frag` was recorded `no_code`, **score 0.0**.
   Four of seven languages were affected — threejs, scene_threejs, glsl_shader,
   opengl_python.  `entry_of(spec)` now reads `ENTRY_FILE`, the canonical table.
2. **Not fixed, and it is structural.**  `bench/_fixed_eval.FixedEvaluator` pins
   `get_runtime(Language.BLENDER)` and its `evaluate` is GLB-centric — build → `measure_glb`
   → `check_connectivity` → `render_glb` → VLM judge.  So on any language that does not
   deliver a GLB from a blender script, every cell still comes back `build_failed` / 0.0,
   and **the only gates it runs are lint and connectivity** — never contract, joint_sweep,
   scene_frames or gl_frames.

The second one sounds fatal for a skills A/B and is not, because of where the numbers come
from.  **`bench/skill_targets.py` reads each arm's harness run** (`<cell>/run/record.json`
and its rendered frames), not the fixed evaluation — and the harness run is the real track,
with all of its own gates.  Verified on the glsl A/A above: every cell was `build_failed`
with `MissingEntryFile`, and the paired target metric still read out
(`mean_edge_density` 0.073 → 0.052 on the first pair).

So for a skills A/B the split is:

| readout | source | works on |
|---|---|---|
| the bundle's target metric (**primary**) | the arms' own harness records + frames | every language |
| the judged score (**secondary**) | `FixedEvaluator` | blender / cadquery only |

which is the right way round, and is why the protocol says decide on the target and merely
report the score.  A driver that had only ever been pointed at a blender battery could not
have told the difference.

### 8.5 The effect wave's result: the target metrics are their own obstacle

Four bundles were to be A/B'd on their own deterministic targets, pinned, one bundle per
run.  **None of the four produced a readable effect, and three of them were decided before
any battery was bought** — which is the point of measuring the floor first.

| bundle | target | what decided it |
|---|---|---|
| `cv3d-glsl-craft` | mean_edge_density | its **own pinned A/A**: paired sd **0.128**, ±2 SE **0.180**, control mean 0.092 → **123 pairs** to resolve a 25% move.  The A/A's identical arms differed by **+0.069**, three times any effect an 8-pair A/B could claim. |
| `cv3d-repeats-and-mirrors` | contract_instance_findings | 132 of 164 corpus runs already at 0; the pinned A/A's one completed pair tied 1.000 → 1.000 |
| `cv3d-urdf-joints` | joint_sweep_errors | arithmetic: 20 of 23 runs at 0 with a tail of 8/50/101, paired sd ≈ 32.6 → **~1420 pairs** for 25%, ~89 merely to drive it to zero |
| `cv3d-scene-composition` | camera_placement_findings | undeliverable (§ below) *and* 0.25 → 0.00 across the corpus → ~512 pairs |

Two things are worth keeping from it.

**Pinning works, and it is not enough.**  Both arms of every pair reported `stage.cached`
on the same `inputs_hash`: one planner call per prompt, one plan, zero planner variance.
And the repeats A/A's one completed pair still scored **0.450 against 0.654 on identical
arms** — a 0.204 spread, indistinguishable from the 0.202 paired sd that 8.1 measured
*without* pinning.  The planner was the dominant term for *plans*; for the judged score
there is a second term of the same size in generation, and pinning does not touch it.

**A shader has no plan to pin.**  glsl's plan is passes and effects, not parts, so the term
`--pin-plan` removes is nearly empty there — which is why the cheapest graphics A/B on the
board still needs 123 pairs.  Pinning helps most exactly where the planner had the most
freedom, and that is the object tracks.

### 8.6 codex gpt-5.6-sol vs gemini-3.7-flash, harness against harness, on graphics and scene

The one-shot comparison of §4 was blender objects.  On 2026-08-26 the same prompts the flash
teaser runs had already scored were run again through the harness with `codex:gpt-5.6-sol` as
the only change — no prompt edit, no profile change — and judged by the same fixed judge.
Six pairs: four graphics (three GLSL, one OpenGL), two scenes.

| prompt | track | flash | codex | Δ (codex − flash) |
|---|---|---|---|---|
| rain_window | glsl_shader | 0.909 | 0.919 | +0.010 |
| aurora_ridge | glsl_shader | 0.940 | 0.936 | −0.004 |
| accretion_disc | glsl_shader | 0.753 | 0.756 | +0.003 |
| murmuration | opengl_python | 0.865 | 0.941 | +0.076 |
| neon_alley | scene_threejs | 0.827 | 0.735 | −0.092 |
| boat_workshop | scene_threejs | 0.747 | 0.837 | +0.090 |

Mean **+0.014**, paired sd 0.065, SE 0.027, 95 % CI **[−0.038, +0.066]**, sign 4/6,
exact p = 0.69.  The largest single delta (0.092) is under half the 0.202 A/A floor of §8.1.
Two reviewers who looked at the pictures blind to the scores split the picture verdicts 3–3.

**What it does and does not establish.**  It cannot answer "does flash match sol" — at
sd 0.202, n = 6 detects only a ±0.21 mean difference, larger than the usable range of these
scores.  What it supports, weakly, is *no sign of a large gap in either direction* on these
tracks: the harness loop, not the generator, is setting the score.  Four confounds are live and
listed so the next run removes them: the plans were not pinned; the 0.70 pass gate stops
whichever arm crosses first and lets the other keep refining, which censors the winner
(neon_alley: codex 1 round vs flash 5); `--candidates` was not pinned across arms; and
best-of-N was inert on graphics for BOTH arms at the time (fixed in 99d13c9).  With plans and
candidates pinned and a fixed round budget, ~32 pairs detect a 0.10 difference at 80 % power.

**The one read that survives the confounds** comes from the pictures, not the scores: the two
models fail differently and consistently.  codex is the tidier renderer and the more literal
clause-follower — flat exact ridge interiors, crisp filaments, no aliasing — and its failure is
a clean image of something *adjacent* to the brief (no city in rain_window; a twilight where a
black winter night was specified; a hoop where a photon ring was).  flash puts the named subject
and the hero effect on screen more often, and fails on craft — aliasing, fringing, grain, a
broken scale.  The judge currently rewards artefact-absence over subject-presence: on
rain_window it scored codex's cityless frame *higher* on brief_fidelity than flash's frame with
the inverted city visible inside every drop, and praised the refine round because "the sharp
building silhouettes are gone".  That is a rubric decision, not a model result, and it is worth
settling before a larger n measures the rubric's preference with more precision.

### 8.7 Head-to-head against the previous harnesses (2026-08-26, first pass)

No such comparison existed before today; their scores are their own judges'.  Protocol:
`bench/h2h_glb.py` (objects) takes 12 confirmed entries of astra3d-brilliana's gallery (prompt
verbatim, `must_have` empty), our runs on the same prompts (flash, ≤ 3 rounds, on a 503-storm
day), renders BOTH GLBs with our renderer, gates both, and judges both with one fixed pro judge
(`static_object_v1`, n=2) — twice: **visual only** (no gate text in the judge's context) and
**gated** (the gate findings in the context, what the harness itself would say).  The gallery
GLBs are merged, un-welded exports whose "floating" findings are export artefacts as often as
defects, so the visual number is the fair headline.  `bench/h2h_scene.py` (scenes) does the
same for five scene_multifile(_graphics) outputs on their authored-camera stills vs our
best-round judge views (`scene_v1`, n=2, stills only).

| | n | Δ ours−theirs (mean) | sd | wins | sign p | their min / $ per item | ours |
|---|---|---|---|---|---|---|---|
| objects, visual only | 12 | **+0.105** | 0.352 | 7/5 | 0.77 | — (curated gallery) | 1–4 rounds, $0.4–4, 13–300 min (storm) |
| objects, gated | 12 | +0.207 | 0.290 | 10/2 | 0.039 | | |
| scenes (stills, one judge) | 5 | **+0.253** | 0.229 | 4/5 | 0.375 | 87.5 min / $11.84 | 40.6 min / $3.17 |

Reading: on pure perception the objects are at parity with their *curated* gallery (+0.105 is
inside the 0.202 paired floor; blender −0.04, cadquery +0.11, threejs +0.34);
with geometry measured we are ahead on 10 of 12 (final, all twelve prompts judged on both sides).  The same GLB re-judged twice moved by up to
0.12 (microscope: 0.893 → 0.771), which is the pro judge's own n=2 variance — read the sign
counts, not the third decimal.  Scenes: ahead on 4 of 5 at less than half the minutes and a
quarter of the dollars; the two landmark prompts (Big Ben, Colosseum) score near zero on both
sides.  Sheets: `bench/out/h2h_brilliana_v1/pairs/`, `bench/out/h2h_scene_v1/sheets/`.

### 8.8 PR #1's articulated planner repair, verified (2026-08-26 evening)

Plan-only pass over all 14 `articulated_v2` prompts with the merged planner (`bench/pin_plan.plan_once`):
**14/14 validated, 0 PlanningError**, 7 needed a normalisation — all of them case 3 (a
sub-part outside its parent bbox → bbox grown); cases 1 (joint on a sub-part) and 2 (degrees)
did not occur in this sample, so they are stochastic.  Full runs of those 7 (flash, 2–3 rounds,
pro judge, storm evening, one shared browser daemon): dutch_door 0.578, parallel_clamp 0.434→0.600,
tool_chest 0.535→0.600, step_ladder 0.600, camera_tripod 0.503→0.600, casement_window 0.08→0.188
, scissor_mirror 0.160→0.148 — **7/7 scored** (the mirror was interrupted once by the pause and rerun).  Before
the repair these plans were `PlanningError` → no result; the repair turns a lost run into a
0.19–0.60 run.  Side findings fixed on the way: a render timeout inside the joint sweep or the
round render used to fail the whole run (07dda4c, 7a7a00a); the planner wrote prose into the
harness-only `normalisations` field (2542f92).

## 9. Reporting checklist

battery name + git sha of prompts; judge id, rubric name + hash, `n_samples`; per-arm
generator/planner ids; rounds and budget caps; per-tier table (n, mean ± std, pass
rate, build-fail rate, cost, minutes); pairwise table; number of degraded verdicts
re-run; **cells dropped as `infra_failed` and `budget_exhausted`, per arm** (§7 — an
omitted drop count is an unreadable table); links to `record.json` / `report.html`
under `bench/out/`.

## Judge experiments log

**2026-08-29 — flash "named-feature sweep" prompt variant: REJECTED.**  Re-judged 9 recorded
runs spanning stored 0.02–0.98 with `gemini-3.7-flash` n=3, base prompt vs a variant that
inserts an explicit present/absent sweep of brief-named features before scoring.  The one
confirmed leniency case (a violin missing its f-holes, judged ~0.43 by pro; renders eyeballed)
did not move (0.898 → 0.888) and mean overall σ doubled (0.014 → 0.032); espresso gained an
honest interpenetration defect, chair/penny unchanged.  Root cause of the violin miss sits in
the PLAN (no FHoles part — see the defining-features rule added to `plan_static.j2` the same
day), not in judge prose.  Flash stays a ranking/fallback judge; pro stays the verdict judge.
Two calibration-tool defects found the same day (colliding `run` labels overwriting judgment
files; old records whose stored overall contradicts their own criterion scores) are fixed in
`judges/calibration.py` and flagged in its report.
