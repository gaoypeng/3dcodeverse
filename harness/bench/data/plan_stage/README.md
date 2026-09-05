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

## `coupled_v1`: does the planner say a mechanism has one input? (2026-09-04)

`bench/prompts/coupled_v1.yaml` is ten one-input linkages — an umbrella, a scissor lift,
a venetian blind, a garage door, a treadle drive, a pram frame, a pantograph mirror, a
drafting arm, a step ladder and a gate-leg table.  200 plan-stage calls (10 x 20), one
arm, `\$7.55`, median 35 s:

    python bench/plan_stage_bench.py --tree . --label coupled_plan --reps 20 \
        --battery bench/prompts/coupled_v1.yaml --out bench/data/plan_stage/coupled_plan.jsonl

**196 valid plans (4 provider blocks), ZERO planning losses, and 193 of the 196 declare a
coupling.**  Rows carry `n_parts` / `n_joints` / `n_mimic`, so the shape of the plan is on
the row and not only in a workspace that is deleted.

| prompt | valid | declared | movable joints (median) | followers | inputs left |
|---|--:|--:|--:|--:|--:|
| `cpl_drafting_arm` | 19 | 19 | 6 | 4 | 2 |
| `cpl_folding_pram` | 20 | 20 | 8 | 3 | 5 |
| `cpl_folding_table_leg` | 19 | 17 | 2 | 1 | 1 |
| `cpl_garage_door` | 20 | 20 | 5 | 4 | 1 |
| `cpl_pantograph_mirror` | 20 | 20 | 6 | 4 | 2 |
| `cpl_scissor_lift` | 20 | 19 | 8 | 7 | 1 |
| `cpl_step_ladder` | 20 | 20 | 6 | 5 | 1 |
| `cpl_treadle_drive` | 20 | 20 | 3 | 2 | 1 |
| `cpl_umbrella` | 19 | 19 | 7 | 6 | 1 |
| `cpl_venetian_blind` | 19 | 19 | 8 | 7 | 1 |

Two readings:

* **Declaring the coupling is not the bottleneck on this battery.**  `articulated_v2`, whose
  prompts do not say "this is the only input", gets couplings in 8 of 14 prompts and a
  median of 3 followers per 6 movable joints; here it is 10 of 10 and the strict one-input
  mechanisms collapse to a single degree of freedom.  The difference is in the prompt, not
  the planner.
* **`inputs left` is a DOF count, not a defect count.**  Read joint by joint on the ten
  recorded plans, eight leave exactly one free joint and it IS the input; the pram's five
  are its fold hinge plus four wheel spins (the fold itself couples handle, seat back and
  both leg pairs), and the pantograph mirror's second is the mirror tilt.  The planner
  declared the coupling correctly in all ten — an earlier reading of this table called the
  pram "3 of 8 declared" and was wrong.

  **This is a hand reading, and it is not reproducible from this directory.**  The rows
  carry `n_parts` / `n_joints` / `n_mimic` only, so the joint-by-joint check was done on
  plan JSON that is not in the tree.  `plan_stage_bench.py --keep-plans` now writes each
  call's `plan.json` beside its row so the next such reading can be re-done; the ten plans
  behind THIS sentence pre-date that flag.

The plan stage is therefore NOT what a full run of this battery would be measuring: it
loses nothing here.
