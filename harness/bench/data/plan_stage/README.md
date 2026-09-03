# `CV3D_PLAN_RESTART`, measured

The rows behind D52's headline: **4.7 % → 0.7 % of plan calls lost, Fisher exact
two-sided p = 0.0067**.  560 plan-stage-only calls (`bench/plan_stage_bench.py`),
`articulated_v2`, 14 prompts × 20 reps per arm, planner `gemini:gemini-3.7-flash`,
both arms in the same window on 2026-09-02/03.

    python bench/plan_stage_report.py bench/data/plan_stage/restart_o*.jsonl

`restart_on.jsonl` is `CV3D_PLAN_RESTART=1`, `restart_off.jsonl` is `=0`; everything else
is identical.  One row per call: `ok`, the error text, `invalid_reasks`, `restarts`,
`missing` (the links a degenerate plan referenced but never listed), `seconds`, `cost_usd`.
Kept in the repo rather than under `bench/out/` because a paper cites the p-value, and a
number whose data is not in the tree is not reproducible.
