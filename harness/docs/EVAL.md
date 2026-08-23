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

Each prompt: `{id, tier, category, prompt, must_have[], dimensions_m?}`.  `must_have`
becomes acceptance items (planner-appended in the loop, fixed-judge checklist in
comparisons); `dimensions_m` becomes `Spec.constraints.dimensions_m` and a
deterministic contract check.

## 3. Harness battery runs (`3dcv bench run`)

```bash
3dcv bench run bench/prompts/static_objects_v1.yaml \
    --generator api-agent:gemini:gemini-3.7-flash --judge gemini:gemini-3.1-pro-preview \
    --rounds 2 --max-usd 2.5 --parallel 4 --out bench/out/static_v1_apiagent
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
    --arms harness:api-agent:gemini:gemini-3.7-flash,harness:gemini-cli:gemini-3.7-flash,oneshot:claude-code,oneshot:codex,oneshot:gemini:gemini-3.7-flash,oneshot+repair:gemini:gemini-3.7-flash \
    --judge gemini:gemini-3.1-pro-preview --out bench/out/compare_v1 [--parallel 3] [--limit N] [--ids a,b]
    [--rounds 3] [--max-usd 2.5] [--loop-judge gemini:gemini-3.7-flash] [--repair-attempts 2] [--gen-timeout 900]
    [--no-pairwise] [--no-resume] [--report-only]
```
Arms:
* `harness:<generator-id>` — the full static_object track (rounds ≤ `--rounds`, ≤ `--max-usd`);
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

Subscription-backed one-shot arms (`claude-code`, `codex`) have estimated or zero USD;
report token counts alongside.  Run them sparingly.

## 5. Comparing backends inside the harness

Same battery, same `--judge`, different `--generator` (`api-agent:*`, `single-shot:*`,
`gemini-cli:*`, `claude-code:*`, `codex:*`, `agy:*`).  Report baseline, final, delta,
pass rate, cost, time per tier; optionally feed the best rounds of two arms through
`PairwiseJudge.compare(spec, renders_a, renders_b)` (`flywheel pairs` already emits
cross-backend candidate pairs keyed by prompt hash).

## 6. Judge calibration

`codeverse.judges.calibration` re-judges recorded rounds without touching the runs:

```bash
python -m codeverse.judges.calibration runs/e2e_chair_blender runs/e2e_cabinet_urdf \
    --model gemini:gemini-3.1-pro-preview --n 3 --out out/calib [--geometry clay|normals|none] [--rounds 0,1]
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
* Never tune rubric text against the battery you report on; bump the rubric version
  (`*_v2`) instead and re-run.

## 7. Reporting checklist

battery name + git sha of prompts; judge id, rubric name + hash, `n_samples`; per-arm
generator/planner ids; rounds and budget caps; per-tier table (n, mean ± std, pass
rate, build-fail rate, cost, minutes); pairwise table; number of degraded verdicts
re-run; links to `record.json` / `report.html` under `bench/out/`.
