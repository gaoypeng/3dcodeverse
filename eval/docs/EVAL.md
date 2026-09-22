# Evaluation protocol

> **Paths in this document.** `bench/…` is relative to `eval/` (this folder's parent); `codeverse3d/…`, `tests/…`,
> `runtime_js/…` and the other `docs/…` files are relative to `harness/`.  Recorded battery output (`bench/out/…`)
> is run data and is not in git.

How we decide whether the harness (plan → generate → gate → render → judge →
refine) beats raw generation, and whether one backend beats another, without
fooling ourselves.  Tools: `3dcode bench`, `bench/compare_backends.py`,
`codeverse3d.judges.{vlm_judge, pairwise, calibration, metrics}`.

## 1. Principles

1. **Fixed judge.**  One judge model + rubric + `n_samples` for every arm of a
   comparison, never the in-loop judge's own score.  Default for decisive runs:
   `gemini:gemini-3.1-pro-preview` (the settings default), `n_samples≥2`;
   `gemini-3.7-flash` is fine for the loop but is lenient/noisy on fine
   distinctions (overall std ≈ 0.08–0.12 at n=3) — its dynamic range comes from
   the rubric defect checklist, not the criteria.
2. **Same evidence for every arm.**  The evaluator rebuilds from code: lint+build →
   `measure_glb` → connectivity gate → canonical 14-view `render_glb` (`OBJECT_VIEWS`,
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
   codeverse3d, blender, node, three, host) are stored per run; keep seeds fixed
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

## 3. Harness battery runs (`3dcode bench run`)

```bash
3dcode bench run bench/prompts/static_objects_v1.yaml \
    --generator gemini-cli:gemini-3.7-flash --judge gemini:gemini-3.1-pro-preview \
    --rounds 2 --parallel 4 --out bench/out/static_v1_apiagent
3dcode bench run bench/prompts/static_objects_v1.yaml --generator gemini-cli:gemini-3.7-flash --judge gemini:gemini-3.1-pro-preview --out bench/out/static_v1_gemcli
3dcode bench report bench/out/static_v1_apiagent
```
Each item = one full `3dcode make` run (its own workspace under `--out`); `results.jsonl`
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
* `agent:<generator-id>` — the **bare agent** (`bench/_bare_agent.py`, 2026-09-22): the same vendor
  CLI and model the harness drives, the one-shot brief + minimal contract, a shell with the
  machine's own tools (Blender, node + three + puppeteer, python + moderngl, glslangValidator) and
  `--max-minutes` of wall clock — and no plan, no 3dcode MCP tools, no cookbook, no gates, no
  judge, no starter library, no deterministic repairs.  It answers "give the same agent the same
  minutes and a Blender": the harness's lift over it is what the harness itself adds.  Smoke
  (dining chair, 15 min): the agent wrote its own test + render scripts, looked at its PNGs, 0.682.
  Two known asymmetries: a session killed by a 503 storm has no single-shot fallback (the harness
  has D68), so on a storm day this arm loses more cells to `infra_failed`; and its USD is 0 when
  the CLI dies before printing stats.

Every arm ends with a `src/model.py` copied into a fresh eval workspace and scored by
the same `FixedEvaluator` (`BlenderRuntime` lint+build → measure → connectivity →
14-view render → `VlmJudge(static_object_v1, --judge, n_samples=2)`).  Then a pairwise
arena runs every harness arm against every one-shot arm per prompt (both orderings).
Output under `--out`: `matrix.json`, `results.jsonl` (cells, resume source),
`pairwise.jsonl`, `cells/<prompt>/<arm>/{run,gen,eval}`, `report.md`, `report.html`.
`--no-resume` archives a harness cell's `run/` as `run.attempt<N>` and regenerates it
(2026-08-30; before that it dropped the recorded row and then resumed the finished
workspace anyway, so the "fresh" cell re-reported its old score).

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

Fixed judge `gemini:gemini-3.1-pro-preview` for every cell; `infra_failed` / outage-text cells excluded (`dropped`), not scored 0; codex USD re-priced from recorded tokens with `codeverse3d.models.pricing` (terra/luna price rows post-date the runs, so `results.jsonl` shows 0.00 for terra).  All 44 codex invocations (24 one-shot argv, 20 harness trajectory argv) carry `--model gpt-5.6-<tier>` and `-c model_reasoning_effort=high`; the un-tiered `oneshot:codex` arm resolves to sol@high via `~/.codex/config.toml`.

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

Pending: rerun the parked sol battery, then terra, then luna, then the claude-code / gemini one-shot baselines on the 4 new prompts (one process, `C3D_MAX_IN_FLIGHT=16`, `--wait-for-provider 240`, `--redo-status infra_failed`).

## 5. Comparing backends inside the harness

Same battery, same `--judge`, different `--generator` (`api-agent:*`, `single-shot:*`,
`gemini-cli:*`, `claude-code:*`, `codex:*`, `agy:*`).  Report baseline, final, delta,
pass rate, cost, time per tier; optionally feed the best rounds of two arms through
`PairwiseJudge.compare(spec, renders_a, renders_b)` (`flywheel pairs` already emits
cross-backend candidate pairs keyed by prompt hash).

## 6. Judge calibration

`codeverse3d.addons.calibration` re-judges recorded rounds without touching the runs:

```bash
python -m codeverse3d.addons.calibration <run-dir> [<run-dir> ...] \
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

* Variance on your own runs: `3dcode judge <slug> --n k` and read `score_std`.
* Sanity anchors: a skeleton placeholder should score ≈ 0.3–0.5; a deliberately wrong
  object should trip the `intent_fidelity` floor / `wrong_object` defect; a floating
  part should cap via `connectivity` findings with `data["kind"]="floating"`.
* A better recurring smoke than re-judging e2e rounds: a separation set with
  deliberately broken variants (exploded / floating / primitive-only).
* **A checklist defect a passed gate measured absent does not cap (7a9b6d3; repaired
  2026-08-30).**  The judge's binary checklist feeds a per-item penalty and, for
  `floating_part` and `interpenetration`, a hard cap (`defect:<id>` in `caps_applied`).
  Those two are also what the connectivity gate *measures*, and since 2026-08-30 the veto
  really reaches both: replayed over the 419 stored static_object verdicts
  (`bench/rejudge_offline.py`) it switches off `interpenetration` on 163 and `floating_part`
  on 72 — before the repair it had fired 10 times in total and never once for
  interpenetration.  Measured 2026-08-26 on a plan-pinned pair
  (fancy_v1 `gas_street_lamp`, two lamps the eye cannot tell apart): the gate reported
  "all 9 parts connected, gap ≤ 2 mm" and was in the judge's input; both pro samples read
  the dark seam under the pedestal as "floating in mid-air, a clear daylight gap", and
  `defect:floating_part` capped the run at 0.600 (uncapped 0.720) against 0.962 for its
  sibling.  `judges/rubrics.measured_absent`: when a `when=gate` cap rule whose `measures`
  names the defect (`penetration_error.measures: [interpenetration]`; `floating_part` by
  default) has all its watched gates passed with no ERROR finding of its `kinds`, the
  checklist claim is switched off before the penalty and before `apply_caps`, named in the
  verdict tail ("checklist claims contradicted by a passed gate") and listed in
  `raw.overridden`.  A failed gate, a gate that did not run, or a defect nothing measures
  (`wrong_object`, `missing_named_part`) are untouched.  Two faults kept it dead until
  2026-08-30: the rule was matched by *id* (`penetration_error` ≠ `interpenetration`, so 237
  interpenetration flags — 110 of them citing only WARNs the rubric says to ignore — were
  never vetoed), and the object rubrics' rules watched `gate: "*"`, so a failing contract gate
  (53 of the 62 blocked cases) switched off a veto connectivity had earned; they now watch
  `connectivity` alone, the only gate that emits floating / penetration ERRORs on this track
  (540 findings over 356 records).  The veto is depth-aware (`VETO_PENETRATION_DEPTH_M`, 8 mm —
  5 mm let the judge's tick on the pipe tee's 5.8 mm designed branch socket stand, −0.32 on that
  side of the paired re-judge): a penetration WARN measured that deep is not "absent" — the
  WARN-blind version switched off 163 interpenetration claims.  With the graded cap below the
  final replay moves 230 of 419 stored verdicts (none down), pass rate 15.0 % → 21.0 %,
  pearson(gate errors, overall) −0.219 → −0.301, vetoed: interpenetration 130, floating 72.  0b6f52b tells the judge the same thing in its prompt; this holds when the judge
  does not listen.
* **`missing_must_acceptance` is graded (2026-08-30).**  The flat 0.6 was the decisive cap on
  121 of 424 static_object verdicts (28.6 %): one unverified must item out of ten scored
  exactly like ten out of ten, and 130 of 419 stored scores sat on 0.600.  The cap is now
  `0.6 + 0.4 · verified/total` over the must items (`CapRule.graded`; the ledger line says
  "k of n must items verified").  Pass/fail is unchanged — any unverified must item still
  fails — only the score keeps its gradient: the 0.600 spike drops 130 → 20 on replay, σ
  0.206 → 0.224.  Every breakdown now carries `scoring_version` (`rubrics.SCORING_VERSION`,
  2 for this batch); `rejudge_offline --identity` holds only same-version verdicts to 1e-9.
* **The judge reads the contact ledger, not WARN prose (2026-08-30).**  Audited over 420
  rounds: P(judge marks interpenetration | connectivity ERROR) = 69/69, and 110 of the 237
  flags cited only WARNs the rubric excuses — the same images with the gate section removed
  flipped the flag on 13/24 sides.  `gates_section` now renders the gate's contact ledger:
  one measured overlap line (deepest pair, through-ratio, "these are welds, not the defect"),
  a MEASURED STRUCTURE block with the plan's joins as contact/OPEN (312 stored rounds carry
  joins; 171 have ≥ 1 OPEN one — the assembly_fit ground truth that did not exist), and the
  lowest point above the floor with its number.  p50 307 / p90 484 tokens on the corpus.
  It rides on the v1 `judge_prompt_hash` (per-run text is not hashed), so its effect is NOT
  in any replay: the measurement is a paired re-judge.  **Run 2026-08-30** (42 matched items,
  the 53-item σ battery as arm A vs the shipped bundle as arm B, fixed order, n=3, $6.12):
  on old-gate-clean items the interpenetration claim rate moved 21 % → 18 % (n=28 —
  underpowered against the corpus's 39 % criterion, which needs the 120 view-pruned rounds);
  Δ(B−A) overall +0.059 mean (corpus rounds +0.141, h2h ours −0.040, h2h theirs +0.026);
  within-arm σ unchanged (0.030 → 0.032).  Two case reads: the new gate's 12.7 mm ERROR on
  clock_q4 is a real catch (0.912 → 0.700), and the pipe tee's −0.32 exposed the 5 mm veto
  line marking a designed 5.8 mm branch socket — which is why the line is 8 mm.
* **The judge's own re-judge σ is 0.035 (2026-08-30, fixed montage order).**  53 items — the
  29 corpus rounds that still carry view PNGs + the 24 h2h object-sides re-rendered from their
  GLBs — judged three times each with the identical prompt (`VlmJudge(fixed_order=True)`,
  pro, $7.79): σ of the final overall mean 0.035, median 0.027, p90 0.060; per criterion
  0.037 (intent) – 0.065 (structure).  That is the number `cost/routing.JUDGE_NOISE` already
  tables (0.030, measured with per-sample view shuffles), so the loop's σ-keyed stops are
  keyed to the right magnitude and the 0.072 round-to-round spread in the corpus is
  generation variance, not the judge.  Untested: temperature 0.0 (brilliana measured 0.013
  vs 0.035 between 0.0 and 0.2 on 512 calls) — one more $8 battery.
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
present.  The graphics loop fixes this (rubric `shader_v2`: likeness,
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
  "no effect" with confidence.  `C3D_SKILLS*` are in `GENERATION_SIDE_ENV`, so the skills
  wave is pinnable; `C3D_PLAN_BRIEF` is not, and `--pin-plan` rejects it.

Pinning is orthogonal to `--aa`, and an A/A that will be read against a pinned A/B must be
pinned too — otherwise the floor carries a variance term the A/B has already removed and
every delta looks smaller than its own noise.

### 8.3 A metric with no headroom is not an underpowered A/B

`bench/skill_targets.py` prints `n to resolve 25%` for each bundle's own target, and for a
count that is mostly zero the number is not a hurdle, it is a refusal.  Measured over the
recorded corpus, before spending anything:

| target | baseline | paired sd (est.) | pairs to resolve a 25% move |
|---|---|---|---|
| `c3d-glsl-craft` / mean_edge_density | 0.234, spread 0.031–0.464 | 0.207 unpaired | ~50 unpaired — pinning + pairing is what makes it affordable |
| `c3d-urdf-joints` / joint_sweep_errors | 6.91 mean, 20 of 23 runs at **0**, tail 8/50/101 | ~32.6 | **~1420** (~89 even to see the metric go to zero) |
| `c3d-scene-composition` / camera_placement_findings | 0.25 on round 1, **0.00** by the last round | ~0.71 | **~512** (~32 to eliminate every fault) |

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
| `c3d-glsl-craft` | mean_edge_density | its **own pinned A/A**: paired sd **0.128**, ±2 SE **0.180**, control mean 0.092 → **123 pairs** to resolve a 25% move.  The A/A's identical arms differed by **+0.069**, three times any effect an 8-pair A/B could claim. |
| `c3d-repeats-and-mirrors` | contract_instance_findings | 132 of 164 corpus runs already at 0; the pinned A/A's one completed pair tied 1.000 → 1.000 |
| `c3d-urdf-joints` | joint_sweep_errors | arithmetic: 20 of 23 runs at 0 with a tail of 8/50/101, paired sd ≈ 32.6 → **~1420 pairs** for 25%, ~89 merely to drive it to zero |
| `c3d-scene-composition` | camera_placement_findings | undeliverable (§ below) *and* 0.25 → 0.00 across the corpus → ~512 pairs |

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
verbatim, `must_have` empty), our runs on the same prompts (generator
**`api-agent:gemini:gemini-3.7-flash`** — the in-process arm, retired two days later by `66175ec`;
`candidates=1`, `texture=false`; ≤ 3 rounds, on a 503-storm day), renders BOTH GLBs with our
renderer, gates both, and judges both with one fixed pro judge
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

**Re-read 2026-08-30, from the stored judgments rather than this table.**  Recomputing
`overall_uncapped` — the rubric-weighted mean of the seven criteria, before any defect penalty or
cap — gives theirs 0.848 against ours 0.843: **Δ = −0.005, 6 W / 6 L**.  So the +0.105 above is
produced *entirely* by the binary defect checklist (their mean penalty −0.192, ours −0.082), and the
checklist is perception, not our gates — `h2h_glb.py:170` judges the visual pass with `gates=[]`.
Its dominant term is `wrong_orientation`: theirs 4/12, ours 0/12, because `contracts/conventions.py`
pins a front axis per language and their pipeline pins only up-axis.  Per criterion we lead
intent_fidelity +0.042 / structure +0.029 / proportions +0.025 and trail assembly_fit −0.069 /
geometry_detail −0.046 / materials −0.042 / craftsmanship −0.029 — right object, plainer object.
Both deltas sit inside the 0.202 floor, so **n = 12 separates neither**; what the run does establish
is that unselected single runs (`candidates=1`, `texture=false`) draw level with twelve entries that
rank **#2–#26 of the 120 scored entries in their gallery** (all ≥ 0.8085 by their own judge; gallery
median 0.7747).  The comparison that would answer the question — our configured best
(`gemini-cli:gemini-3.7-flash`, `--candidates 3 --texture`) against a *median* draw from their
gallery — has not been run.
**Measurement caveat found 2026-08-30 (evening):** the three THEIRS threejs sides (desk_lamp,
clock, lighthouse — THREE.GLTFExporter files with a root ``pivot`` matrix, 21–173 unnamed nodes
and duplicate names) were MIS-MEASURED by trimesh: an upright lamp read as lying on its side
with a part 28 mm under the floor, and the judge saw that measurement table.  Their
``wrong_orientation`` ticks and the ground-gap numbers on those three are suspect, and so is
the threejs +0.335.  Our own 14 threejs GLBs are unaffected (0 mm difference between the
graph walk and trimesh).  ``measure_glb`` now walks the edge matrices itself and flags
duplicate/unnamed nodes; those three sides need a re-evaluation before the per-language
threejs number is quoted again.

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

**2026-08-29 — profiles and the detail round.**  `RoundPolicy.detail_rounds` is tri-state
(`None` = the track default, `lifecycle.DEFAULT_DETAIL_ROUNDS=1` where supported; `0` = off).
Until this date a profile that injected a `judge_samples>1` policy (economy, quality)
silently zeroed the static track's surface-detail round while balanced kept it; all
three profiles now get it, and `C3D_DETAIL_ROUNDS` remains the A/B switch.  Any
economy/quality-vs-balanced comparison straddling this commit compares different
round counts.

**2026-08-29 — per-language system prompts: NULL, three independent A/Bs.**  The
one-line system prompts were replaced with evidence-grounded ones mined from each
language's recorded failure corpus (the `C3D_SYSPROMPT=v0` arm kept the old ones for
the A/B; arm and `system_v0.md` files were deleted 2026-08-29 after the null).  Three
paired A/Bs all read null: glsl CLI (n=10/arm, Δ+0.003, within-arm σ 0.18), blender
single-shot (8 pairs, paired Δ−0.027), blender CLI on 3.6-flash (10/10 pairs, paired
Δ−0.036, 4W/2T/4L, paired σ 0.403 — per-brief swings up to ±0.9 dwarf any prompt
effect).  With the tool loop enforcing the self-check discipline anyway, prompt
wording is not where static/graphics quality lives; run-to-run variance is.  The
prompts stay (they cost nothing and encode true contracts), but no further wording
A/Bs without a structural change to test.


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

### 2026-08-30 — scene stack iteration: five levers, four batteries, one honest ledger

**Cross-project baseline (renders-only, our `scene_v1`, one judge):** scene_multifile_graphics
mean **0.433** (n=29, max 0.936, six runs ≥ 0.7) vs our pre-iteration scenes **0.272**
(n=24, max 0.516, zero ≥ 0.7).  Every number below is the same six ToD-explicit briefs at a
110-minute window unless noted; per-brief deltas at n=1 carry judge noise σ≈0.4 — only arm
means and mechanism evidence are read.

**Levers landed** (each commit message carries the measured motivation):
env-skeleton lint ERROR (251d099); boot-time settle (35168da) + slope-conformal guard
(e71221a); assets ∥ env (1ddea7f); plan-aware contract gates (1bdfd32); L2 zone layouts
(beb6605) + camera clearance (58da6b3) + `C3D_ZONE_LAYOUTS` switch (020316d); opt-in camera
repair (0365fef, `C3D_CAMERA_REPAIR=1`); opt-in auto-exposure (78d397c,
`C3D_AUTO_EXPOSURE=1`).

**Longitudinal arms:**

| arm | config | vs prior arm |
|---|---|---|
| t36 | 3.6-flash, new ToD prompt | mean 0.483 (its own 75-min baseline was 0.259) |
| s37 | 3.7-flash + settle | Δ+0.029 vs t36 (n=6, 4W2L; nyc_dusk 0.718 = first `passed`) |
| fv  | + camera-BLIND layouts + gates + ∥ | Δ−0.161 vs s37 (n=4, 0W3L1T) |
| fv2 | camera-AWARE layouts | Δ−0.036 vs s37 (n=5, 1W4L; izakaya **0.758 passed, best scene ever**) |

**Settle A/B** (re-render six finished workspaces, only variable = settle): 3W2L, mean
+0.063 — inconclusive at n=1, but the mechanism evidence is decisive: the slope guard cut
santorini's moves 17 → 3 (six hillside stairways, once lifted +1.3..+3.3 m and judged 0.0,
now refused), and small uniform reseats (+0.06..0.26 m) are what the wins are made of.

**Layout-layer anatomy** (why fv regressed, three distinct modes): camera-blindness — the
director placed BarCounter 0.7 m from a lens, three rounds of camera_in_geometry, FIXED by
putting cameras in the layout prompt + validator; internal overlap — RetainingWall ×
PrayerBench interpenetration, OPEN (a naive pairwise-distance rule false-positives on
legitimate adjacency like stools against a counter); richness variance with no caps at all.
Contract-gate lifecycle verified end to end on fv_nyc_dusk: r0 `missing_content: 2` → the
refine round fixed both → final round clean, settle moved nothing, 0.724 passed.

**Standing verdict:** the layout layer is net ≈ null after the camera fix and stays ON
(one-var off-switch exists); camera repair + auto-exposure ride in `scene_px_v1`
(vs fv2 same-brief = their isolated read, in flight).  The honest gap to the baseline
project is no longer the mean — it is the ceiling (their 0.936 vs our 0.758) and the
floor (their 6 runs ≥ 0.7 vs our 2).

**Addendum (same day, later).**  fv2 closed at n=6: the layout layer vs s37 is Δ−0.023 ≈ null (2W4L; izakaya +0.258 and alpine +0.047 are the wins).  scene_px_v1 (camera repair + auto-exposure armed — verified in /proc of the live workers): isolated px−fv2 Δ+0.055 (3W1L, n=4, taj pending), arm mean 0.579 with 3/4 runs ≥ 0.6. The trigger sweep attributes that delta to VARIANCE, not to the levers: camera repair fired zero times across the battery (the layout layer's camera clearance already keeps lenses out of furniture upstream), and auto-exposure fired only on taj, where halving the exposure left the blown frame's luminance unchanged (0.8369 → 0.8369 — effectiveness investigation open; suspects: NoToneMapping renderers, toneMapped=false materials).  Decisions: camera repair defaults ON (3f463ce — provably a no-op when healthy, and the class it insures against is fatal and unreachable by refine); auto-exposure stays opt-in; layouts stay ON behind the one-var switch.  Next: scene_final_v1 — all 20 briefs on frozen defaults (3.7-flash + settle + layouts + camera repair, AE off) to meet the baseline's n=29 at comparable sample size.

**Addendum 2 (2026-08-31).**  scene_final_v1 closed: all 20 briefs on the frozen defaults, n=19 scored (fishing_harbor died at r1 on budget with no judgeable views), stored mean 0.533, four runs `passed`, four ≥ 0.7 (wormhole 0.784, alpine 0.761, eiffel 0.739, souk 0.715).  The same-scale verdict — renders only, one judge, our `scene_v1` on both corpora: **ours n=18 mean 0.488 med 0.467 max 0.802, ≥ 0.7: 3** vs the baseline project's n=29 mean 0.433 med 0.404 max 0.936, ≥ 0.7: 6.  Mean and median are AHEAD of the baseline for the first time (from 0.272 at the start of this iteration); the ceiling (0.802 vs 0.936) and the ≥ 0.7 rate remain theirs.  px closed with taj at 0.126 (budget), pulling the isolated P5+P6 read to null — consistent with the variance attribution above.  The closing defect histogram (flat_ground 12, world_edge 11, undressed 11, thin_atmosphere 10, monotonous 10) chose the next lever: the density gate (layout budgets vs census instance counts) shipped the same day.

**2026-08-31 — judge payload v3: the 14-view rig ADOPTED (D47).**  4-arm A/B on 42 items
(18 corpus rounds + 24 h2h sides) × n=3 `gemini-3.1-pro-preview`, plus full 3.7-flash and
3.6-flash replicas, ≈ $47.  Arms: A = 8 views no clay (old baseline) · A2 =
production-faithful 8 views + clay · B = 14 labelled 640 px single views · C = 14-view rig
+ clay through the montage machinery (5 montages + 2 crops).  C mean overall 0.6835 vs A
0.6427 / A2 0.6558 / B 0.6308; the only multiplicity survivor is same-cap C−A +0.038
(n=29, SE 0.013, t≈2.98) — raw C−B p=.041 / C−A p=.024 do not survive Holm because cap flips make
the deltas heavy-tailed.  `untextured_flat` cap-rule fires: 11(A) / 6(A2) / 8(B) / 3(C).
B rejected at $0.198 & 67.5k tok/verdict: its deficit is entirely cap flips (~half
contradicting the pixels), and a defect-provenance pass over every PRESENT vote showed its
defect-hunter halo was text-quoting — interpenetration pure-view TP is 0/14 in EVERY arm
(the naive 14/14 measures reading comprehension of the shared gate text), and B's floating
lead reduces to one genuinely visual item.  Flash replicas: payload Δ ≈ 0 on both 3.7 and
3.6 — the rig pays only at pro tier.  C ships as D47 ($0.155/verdict, 40.9k input tok) — with the SHIPPED
grouping re-measured (arm Cprod, $7.2): grouping alone moves the mean −0.044 vs C's
accidental grouping (windmill 0.51 → 0.04; clean/dirty gap 0.134 → 0.155; ≈ A2 overall at
−0.016) — a payload experiment must measure the exact grouping it ships.  Underside
full-res singles (arm Cplus, $6.8): recovers the espresso underside catch (0.795 +
render_artifacts vs 0.965 blind) but posts the worst clean/dirty gap (0.112), the highest
row-σ (0.040) and a windmill relapse to 0.62 — rejected; the replace-the-bottom-crop
variant stays queued.  Temperature A2 @ t=0 ($6.0): σ 0.037 → 0.022 (8/42 rows exactly
deterministic) but mean −0.037 and pearson(gate errors) −0.195 → −0.126 — t = 0.2 stays.

### 2026-08-31 — the loop goes four-track: objects, articulated, graphics join scenes

Same methodology per track: a fancy battery → the defect histogram → deterministic
levers → a paired validation arm.  First-day ledger (3.7-flash, stored scores):

| track | battery | arm | standing defects |
|---|---|---|---|
| graphics | gfx_fancy_v1 n=6 | mean 0.692, max 0.910 (caustics) | banding 3/6, comb 1 |
| articulated | art_fancy_v1 n=6 | mean 0.573, 3 ≥ 0.7 (lamp 0.892) | detail_below x3, wrong_motion x2, pivot x2, pose_clips x2 |
| objects | obj_fancy_v1 (open) | birdcage 0.982 r1 | complex briefs blow the round-0 budget (40→60→90 min) |
| scene | scene_dg_v1 n=6 | density-gate arm: 3W2T1L vs fs_ | gate fired ZERO times — quiet insurance; zone_empty x3 did the catching |

**The graphics lever validated cleanly.**  'Dither last' (a scored-defect rule the
cookbook buried mid-comment) moved into the system prompt's survive-every-brief rules
after two placements were rejected by the tests themselves (a new always-on chapter —
and even comment growth — eats the chapter-selection budget and squeezed the stars
recipe out of the aurora prompt).  Validation re-ran the three weakest/strongest:
accretion 0.846→0.911, aurora 0.478→0.617, campfire 0.328→0.726 — **+0.201 mean,
3W0L, banding 3/6 → 0/3**.

**Articulated planner repairs went live.**  Repair 4 (root_link that names no part)
and repair 1c (a joint naming a sub-part by word subset — {glazed,door} ⊆
{glazed,front,door}) each turned a twice-dead planning failure into a scored run:
grandfather_clock 0.815 on its first repaired attempt; the excavator cleared planning
and moved its death downstream to budget/model-timeout.  rc=247 (a gemini-cli
process crash with an empty response) appeared twice on the heaviest sessions and did
not reproduce on retry — judged transient.

Next levers by histogram: articulated joint quality (pivot/pose/motion-type — the
planned-motion and sweep machinery already measures most of it), object round-0
budgets for ship-class briefs, and the scene ceiling (0.802 vs 0.936).

**2026-08-31 — conditional cross-section slices ADOPTED (D48): three iterations under a
pre-registered stopping rule.**  Question: can the judge be made to SEE interpenetration
(D47's provenance pass: pure-view TP 0/14 in every arm — the 14/14 naive figure measured
reading comprehension of the shared gate text)?  Instrument: the same 42-item battery,
n=3 pro, 14 rows conn-dirty; the metric is the **guarded image-cited interp majority**
on those 14, bar fixed at 8/14 BEFORE the last iteration ran.

*Provenance-guard methodology.*  Every interp PRESENT vote that cites a slice is checked
against the slice's own manifest and, where decisive, the PNG.  Citation classes:
**A** = the slice is named AND the named part pair is verbatim in that slice's hatched
list; **B** = the slice is named with no pair, but the slice genuinely carries hatch
("slice 1 shows massive red hatched areas" over a slice that does).  A cited slice with
no hatch, or a cited pair the manifest contradicts, is a FAIL and the vote is discarded.
v3: 13 A votes + 8 B votes counted, 2 FAILs (a pair-swap parroted from the gate text; a
citation into an empty slice) — 2/23 fabrication, 0 rows flipped by the guard.  Two rows
reach majority through B votes only; both were PNG-read and the hatch is real, so 8/14
stands (a pair-named-only reading gives 6/14 and discards PNG-true citations).

*The three iterations.*  **v1** (slices always on, primed rig text): 8/14 but the wording
primed the defect and clean rounds took damage — rejected.  **v2** (de-primed: hatch only
gate-ERROR pairs, neutral legend, anti-over-read sentence, degenerate-slice drop): clean
damage gone, but 5/14 — the seeing survived (catches retained), the narration did not.
**v3** (conditional on a connectivity gate ERROR + one neutral elicitation sentence in
the defect-checklist bullet; 28 clean rows byte-identical CPROD by construction): **8/14
guard-verified**, honest negatives on the 4 rows whose error pairs miss both centre
planes (10/12 present-votes there say text-only rather than inventing a citation), both
real catches retained (gate_valve wrong_orientation 0.700→0.500 at 3/3; clock_theirs
hatch-verified 3×A), dirty mean at CPROD parity excluding one row, clean−dirty gap
0.155→0.198, dirty verdict $0.154 vs $0.172 (cheaper; docs/COST.md §14).  Caveat carried
into D48 as a watch item: b36_v0_02 collapsed 0.54→0.008 — every added defect was a
minority vote in the other arms and render-true, but the elicitation's per-defect sweep
made three samples consistent and the multiplicative cap stack did the rest; the first
production battery re-checks dirty-round defect rates and cap stacking before D48 is
called done.

**Day-two addendum (2026-08-31, later).**  The articulated lever landed: the
motion gate now computes the EXACT axis from the pivot geometry (59d156d —
anti-parallel keeps the provably-right negation, orthogonal states the computed
`<axis xyz>` verbatim; URDF space is Z-up, learned the hard way in the test).
Validation arm art_axis_v1 re-runs swiss_knife (0.356 — it failed the same joints
three rounds straight under the generic hint), metronome (0.214) and umbrella
(0.334) at the raised 95-minute budget.

**The retirement board** — briefs that defeated every ceiling, each with a distinct
death spectrum: excavator (PlanningError → 75.8-min budget → read-timeout →
hard-watchdog, 4 deaths), drawbridge (rc=247 → hard-watchdog → read-timeout, 3),
dragon_teapot (116/65+/95.2-min budget kills, 3), carousel (65/72/96.8, 3).  The
reaper now saves each corpse's last three events before deleting, which is how
these spectra exist at all.

**The microscope autopsy** (obj_fancy_v1, scored 0.0): the render shows two floating
grey boxes — a mid-session death shipped a stub, connectivity flagged the floating
'Limb' at 290 mm, refine was planned correctly (6 tasks), and then the refine
session itself died of rc=247 (a 503 inside gemini-cli) so no_change delivered the
stub honestly.  That crash signature has now killed three sessions tonight; a
one-retry-on-transport-failure policy in the round loop is queued behind the axis
validation arm.

Closing arm stats: articulated n=6 mean 0.573 (3 ≥ 0.7; histogram detail_below x3,
wrong_motion/pivot/pose_clips x2 each), objects n=3 scored (birdcage 0.982,
gramophone 0.909, microscope 0.0) with ship-class briefs consuming whole budgets
unscored — the fancy-object round-0 cost lesson is now three budget raises deep
(40 → 60 → 90 minutes).

### 2026-08-31 — day three: both fancy arms closed; the axis-instruction verdict funds a deterministic repair

**obj_fancy_v1 closed** (6/6, one retirement round earlier): scored n=4 —
birdcage 0.982, gramophone 0.909, tall_ship 0.276, microscope 0.0 (mean 0.542);
armillary + pipe_organ ended `budget` unscored.  tall_ship autopsy: blender lint
findings exploded 1→4→14 across rounds while connectivity stayed at 3 and
materials stayed flat — the brief survives (softened wording), the defect is
execution depth, not planning.  Model note: tall_ship's scored run rode
gemini-3.6-flash during the 3.7 outage.

**art_axis_v1 closed — the exact-axis INSTRUCTION alone loses.**  0.542
(metronome, best=r0) / 0.276 (swiss_knife) / 0.0 (umbrella, r2 budget) vs
baselines 0.214 / 0.356 / 0.334: paired Δmean −0.028, 1W2L, and
`wrong_motion_type` present in 3/3 final rounds.  The mechanism read is the
real result: the gate's hints were correct and specific (metronome carried the
negate hint r0–r2; swiss_knife carried verbatim `<axis xyz>` values all
battery) and the agents applied none of them.  Measurement existed; execution
didn't follow → shipped `dd88944` deterministic axis repair
(`repair_motion_axes`: anti-parallel → negate authored axis, orthogonal →
write suggested_axis; runs before the sweep so poses/renders/judge see the
fix; INFO finding tells the agent not to undo it; `C3D_AXIS_REPAIR=0`).
Confounds recorded honestly: this arm ran on 3.6-flash (3.7 outage, 000×3
probes), and `21c34c1` transport-retry landed mid-battery (these runs predate
it).  Validation arm `art_axr_v1` (same briefs, 3.7, both levers live) is in
flight; its row decides the lever.

### 2026-08-31 — D48 watch item: it is the images, the caps never bind, and the channel is the more accurate arm

Rebuilt corpus (the original scratchpad was destroyed by a `/tmp` cleanup — a fresh selection,
not a replay): all 223 `static_object` runs under `bench/out` carrying a GLB + plan re-gated live
(0 failures → 92 dirty / 131 clean); 16 dirty + 6 clean controls re-rendered on the D47 14+4 rig
and judged through the UNPATCHED shipped code, three arms × n=3 `gemini-3.1-pro-preview`
($11.45, 0 errors, every row `n_used=3`).  Payload drift vs the measured shim was closed at the
implementation review, not re-run.

| dirty n=16 | mean | median | marked/case | hard-cap loss | defect penalty |
|---|---|---|---|---|---|
| `off` (pre-D48 payload) | 0.468 | 0.546 | 2.50 | 0.0204 | 0.1888 |
| `slices` (shipped) | 0.429 | 0.447 | 2.75 | **0.0149** | **0.2040** |
| `elicit` (sentence only) | 0.471 | 0.506 | 2.38 | 0.0148 | 0.1819 |

`elicit` is the shipped `slice_payload` returning `([], True)` — the state a crashed drawing
already produces — so `elicit − off` isolates the SENTENCE and `slices − elicit` the IMAGES.  The
sentence is inert on dirty rounds (+0.0035, and it *lowers* marked defects); the images carry the
move.  The cap stack is not the mechanism the watch item feared: hard-cap loss FALLS, is exactly
0.000 on 12 of 16 rows in both arms, no row loses > 0.1 to a cap `off` did not apply (max +0.032),
and no cap brought by a new mark binds.  b36_v0_02 moves 0.579 → 0.202 (not 0.54 → 0.008) with a
0.000 cap contribution.  The mean move is inside noise (bootstrap 95 % CI [−0.114, +0.035];
9 down / 5 up / 2 tie).

Every one of the 16 majority-marked disagreements was adjudicated against the rig renders and the
exact slice PNGs the judge saw: **+6 true marks, −0 true marks, −6 false `off` marks, +4 false
marks** — net true +6 / net false −2.  Two of the four false gains are D48's own drawing
describing itself, and both are now fixed in slices-only text: the legend suffix
`[outline: open section]` (read as a hole report; it alone moved `holes_or_inverted_faces` 0 → 2
cases) is now `[outline only — not filled; NOT a hole]`, and the in-plane caveat now points at the
shaded and geometry views for `floating_part` / `holes_or_inverted_faces` instead of denying the
slice — a bare prohibition would suppress the true marks the channel exists to win.  Clean-row
identity is now measured rather than inherited (all 6 controls rebuild byte-identical under both
dials); forcing the sentence onto clean rows costs −0.092, which the on-error gate prevents.
Open, not D48's: `untextured_flat` is over-applied by BOTH arms on shaded models with a uniform
sensible colour — exactly what the rubric item's own text exempts.

### 2026-09-01 — `untextured_flat` wording: the exemption becomes an operable test (adopted)

Both D48-battery arms over-applied `untextured_flat` to shaded models with a uniform sensible
colour — the case the item's own text exempts; the judges were reading it as "no texture map".
Wording A/B on the same 22-case corpus, OLD = the stored `off` arm ($0), NEW = one re-judge with
the sharpened text (n=3 pro, $3.99), drift calibrated against the near-A/A band (`elicit−off`:
same payload plus one inert sentence).

Pre-registered gates: (1) adjudicated-false marks drop — sv2 violin 2/3 → **0/3**, clean-control
minority 1/3 → **0/3**, but the lapstrake boat holds 2/3 → 2/3 (its per-plank tone variation is
invisible at montage scale — a payload-resolution limit, not wording); (2) the true default-grey
mark holds — office chair 3/3 → 3/3, protected by the new "a defect even when lit" clause;
(3) collateral drift is NOISE: newword−off meanΔ −0.0212 with 4 movers > |0.1| vs the near-A/A
band's −0.0225 with 6 movers, and the big movers are the SAME cases moving the SAME way in both
re-rolls (c02 −0.254 in both; d10 −0.337/−0.338 — the re-rolls both catch the armrest floating
the original `off` roll missed, so the "drift" is the baseline's own vote variance).
`untextured_flat` votes overall: 8 → 5, the drop landing exactly on the adjudicated-false cases.
New text: default grey / magenta placeholder (the untinted "nothing was assigned" look — a defect
even when lit), or UNLIT fills where differently-angled faces render the SAME brightness; "no
texture map" is never the test.  `judge_prompt_hash` moves for `static_object_v1`, as any wording
change does; scoring is untouched.

### 2026-09-01 — sceneloop: the scene-track efficiency ledger, the first skills A/B (inconclusive), and two refuted hypotheses

An 8-hour self-paced loop over the scene/graphics track.  What was MEASURED (all artifacts in
the session sceneloop scratchpad; runs under its runs/ dir):

* **Efficiency ledger, 124 stored scene runs** ($349 total): $2.82/run, 76 min model latency.
  assets $112.7 (32%, 60 calls/run, 64% cached) is the money whale and 39 min/run the time
  whale; refine/zones agent sessions carry 0.4-2.2M input tokens each at ~90% cache; judge
  $28 total.  Decomposition: asset first-shots $80.2 / retries $32.6 (rate 0.27 on 3.7-flash)
  — RETRY-RATE REDUCTION is the ranked lever.
* **Refuted from stored data ($0):** 3.6-flash for asset single-shots — retry-rate 0.39 vs
  3.7's 0.27 AND $/first 0.0184 vs 0.0116 (era confound noted).  3.7 stays the asset model.
* **First skills A/B** (the 4 ported bundles, C3D_SKILLS_ONLY, 6 prompts x {off,on},
  generator gemini-cli:3.6-flash under provider degradation, planner pro, single run per arm):
  meanΔ(on−off) = −0.068, pairs [koi −0.07, neon −0.49, lake +0.25, alley −0.53, ruins 0.00,
  snow +0.45], sd(d) ≈ 0.39 → a ±0.1 effect is UNRESOLVABLE at n=6.  Attach integrity was
  verified (on-arms 2-4 skills.attached events, off-arms zero).  Low on-arm rounds died of
  classic camera/dark failures, not visibly of the recipes.  VERDICT: inconclusive — the
  bundles stay `inherited-unverified` and OFF; the next attempt should be n≥20 pairs or
  per-recipe instruments (bake-orientation census, metallic-water census) instead of
  end-to-end score.
* **codex generators on scene (n=2, planner+judge pro):** gpt-5.6-sol 0.0, terra 0.008 vs the
  flash corpus mean 0.412/median 0.399 — structurally complete scenes that are visually dead
  (dark frames, placeholder materials, white-box assets); sol needed >55 min for round 0.
  Suggestive that the recipe/skill layer matters MORE for codex; not proof at n=2.
* **Trap parity vs scene_multifile_graphics**: their extra check_shaders audits (logdepth
  chunks, fog chunks) are THEIR renderer's contract (log-depth on, fog mandatory); ours
  defaults logDepth=false and detects fog handling already — coverage ≥ theirs for our
  contract, no code change.
* **QUIET_KINDS lesson**: asset sessions attach no standing skills BY DESIGN (prompt tax on
  ~60 calls/run); the asset-side lever is a compact signal-conditional excerpt in
  scene_asset.j2 — designed, not yet built or measured.
* **Watch item (n=1)**: a codex plan's scene-wide translucent volume (HazeBands) drew 6/10
  placement sunken-into ERRORs; 0/120 stored errors have non-solid targets, so no fix yet —
  the designed fix (material-evidence non-solid container exemption in host_placement.mjs)
  ships only if this recurs.

