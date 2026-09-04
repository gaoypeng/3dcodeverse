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

`plan_once` runs the plan stage WITHOUT `Track.run`, so there is no run ledger and the
workspace is deleted: these rows are the record, and each carries its own `cost_usd` (the
script prints the total).  `--keep-failed DIR` keeps the workspace of a call that produced
no plan, which is how the `parent == child` class below can be read.  Rows recorded before
2026-09-03 carry no `started_at` / `tree_commit`; later ones do.

## The trigger, narrowed (2026-09-03)

Review narrowed the restart to the class the 200-call survey actually found — a
**collapsed** plan (one top-level part, a third of the floor or less) that ALSO references
links it never lists — and stopped it spending one of the two validation re-ask slots.
Three arms, **one window**, same battery and planner:

    python bench/plan_stage_report.py bench/data/plan_stage/trigger_{off,wide,narrow}.jsonl

`trigger_off.jsonl` is `CV3D_PLAN_RESTART=0` (140 calls, 14 prompts x 10 reps);
`trigger_wide.jsonl` is the trigger as D52 measured it, run from a worktree at commit
9506737 (280); `trigger_narrow.jsonl` is the narrowed one (280).

| arm | judged | losses | rate | dangling-link | `parent == child` | other |
|---|--:|--:|--:|--:|--:|--:|
| off    | 140 | 4 | 2.9 % | 3 | 1 | 0 |
| wide   | 275 | 6 | 2.2 % | 4 | 2 | 0 |
| narrow | 276 | 5 | 1.8 % | **0** | 4 | 1 |

(`other` is one `BudgetExceeded`: a harness budget ceiling, which the first pass had filed
as provider weather and dropped from the denominator.  `outcome()` now counts anything
that is neither a `PlanningError` nor a named provider failure as a loss.)

**No pair separates on the overall rate** (Fisher 0.45–0.75), and this window did not
reproduce D52's headline at all: its own control arm loses 2.9 %, not 4.7 %.  The
separation is inside the class the mechanism targets: **off 3/140 vs narrow 0/276,
p = 0.038** (the `dangling_link` column of the class table the report prints).  Read the
class, not the total — and read the order it was found in: the three overall-rate tests
came back at 0.45–0.75 FIRST, and the class split was looked at afterwards, so 0.038 is an
exploratory result on a pre-specified mechanism, not a pre-registered test.

Two secondary readings, both consistent with the change:

* All four of `wide`'s dangling-link deaths carry `restarts=1` and died at the validation
  cap — the re-ask slot the restart used to consume.  `narrow` has none.
* The narrowed trigger fires on 28 of 280 calls against 38, and recovers 27 of 28 (96 %)
  against 33 of 38 (87 %): it stops re-sampling plans an ordinary re-ask repairs.

The residue is a **different failure class** that nothing here addresses: a joint whose
`parent` and `child` are the same link (1 / 2 / 4 across the arms, flat).  Those calls
never restart — the plan lists every link it names — and the model rewrites the same
joint through all three re-asks.  That is the next thing to measure, not a regression of
this one.
