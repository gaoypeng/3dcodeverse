# The plan-loop wave — what it measured, and why it kept nothing

2026-08-24 → 25.  Goal: stop hard-coding plan size ("6 to 14 parts") and let the plan come
from the model's common-sense knowledge of the real object, with graph/hierarchical
structure where it earns its keep — and prove the addition is a **guaranteed non-negative
improvement** before shipping it on.

**Outcome: nothing was kept.**  Not because the ideas were wrong, but because the wave
measured its own instrument first and found it could not resolve them.  That measurement is
the wave's real product and it now governs every A/B in this repo (`docs/EVAL.md` §8, §8.1).

---

## 1. What was designed

The design agent mined 47 recorded plans and found the ordering fact for everything after:

| plan-side quantity | vs assembly_fit | vs overall |
|---|--:|--:|
| **undeclared pairs overlapping > 10 mm** | **−0.50** | **−0.50** |
| declared pairs with a planned gap | −0.38 | −0.28 |
| number of parts | −0.13 | −0.11 |

Plans with ≥ 1 pair of parts that overlap but that the plan never says touch: `assembly_fit`
0.683, 1.82 gate errors.  Plans with none: **0.811, 0.22**.  **Part count does not predict
fit; undeclared planned overlap does.**  Six changes were ordered from that, C1…C6, each
behind one switch (`codeverse/tracks/plan_features.py`, `CV3D_PLAN_FEATURES`).

## 2. What happened to each

| id | change | outcome |
|---|---|---|
| **C0** | (not a change) the A/A calibration | **the wave's most valuable result** — see §3 |
| **C1** | `fit` — plan-level fit check, one repair re-ask | **REVERTED.** 2 regressions, mean Δ −0.020, and 3 of 8 variant cells died outright with `PlanningError: part OuterShell touches unknown part CarriageLever`. It turned a recoverable planning slip into a total loss. Patch kept at `bench/out/plan_loop/C1/C1_fit.patch`. |
| **C2** | `contacts` — every declared contact rendered to the builder with computed mating faces | **NEVER MEASURED.** Implemented, offline-green (15 tests), reverted from the tree per the loop rule. Patch at `bench/out/plan_loop/C2/C2_contacts.patch`. |
| C3–C6 | graph edges, graph budget, graph example, consistency | not reached |

C1 was attempted four times.  Attempt 1 gave the revert above; attempt 2 was discarded
(`invalid_attempt1_maintree_import`) after the A/B was found to be running the MAIN tree's
harness in **both** arms — comparing a tree with itself while looking healthy; attempt 3 was
discarded (`invalid_attempt2_touches_hardfail`); attempt 4 lost **11 of 16 cells** to a
provider 503 window and produced zero paired prompts.

## 3. The instrument, measured

An 8-prompt **A/A** — arms identical by construction — on the same battery, same judge:

* paired **sd 0.202**, SE 0.082, 2 SE band **±0.165**;
* the rig's own conclusion, printed in its summary: **~408 paired prompts** would be needed
  before a +0.02 decision threshold is resolvable;
* worst pair `mech_hard_pitcher_pump`, **identical settings**: one arm planned **1 part**
  (`WoodenPlatform`) and scored **0.750 / passed**; the other planned **10** and scored
  0.600.  3 vs 13 built parts, 4,796 vs 61,340 triangles.

So the variance is **in the planner**, not the judge — the two arms were not two
measurements of one artifact, they were two different artifacts.  More judge samples cannot
touch it.  This is why the wave's premise (8-prompt A/Bs screening ±0.02 changes) could
never have worked, and why stopping was the right call rather than a fifth C1 attempt.

## 4. What shipped from it anyway

* **`bench/ab_plan.py`** — the paired control/variant rig: arms launched together so the
  weather matches, `infra_failed` excluded not zeroed, `--aa` calibration mode, and a
  **Confidence** block (paired sd, SE, 2 SE band, `separated from noise: yes|NO`, and
  `n_for_power`) printed beside every verdict so a screen can never read as a proof.
* **`bench/ab_gate_rates.py`** — the same run's *deterministic* readouts paired per prompt,
  because a contact-table change targets penetrating pairs, not a judged score.
* **`codeverse/tracks/plan_features.py`** — one switch per change, so each is A/B-able alone.
* **`docs/EVAL.md` §8 and §8.1** — the noise floor and where it comes from.
* Two defects found in the rig itself while trying to use it: an A/B launched from a
  worktree silently ran the main tree's harness in both arms, and `--report-only` rebuilt
  `summary.md` from bare flags, relabelling a real A/B as an A/A in the one file that
  outlives the run.

## 5. Next, precisely

**Pin the plan.**  For a change that acts *after* planning, plan once per prompt and seed
both arms with the same `plan.json`; the paired delta then stops carrying the planner's
spread.  It costs one planner call *less* per pair, not k times more.

The decision procedure is implemented and tested —
`codeverse.tracks.plan_features.pin_plan_blockers(variant_env)` returns the switches that
forbid sharing a plan, and refuses anything it cannot prove acts after planning (refusing
costs one noisy A/B; pinning wrongly costs a confident wrong answer).  By that rule **C2
`contacts` is pinnable and C1 `fit` is not**.

Still to build: the seeding itself in `ab_plan.py` (`--pin-plan`) — run the plan stage once
into a shared dir, then copy `plan.json`, `stages/plan.json` and the `run_state.json` stage
entry into each arm's run workspace before spawning it.  The stage cache keys on an inputs
hash computed from the spec, which is identical across arms, so a seeded arm skips planning
and resumes from it.  Then re-run C2 with `--pin-plan` and read out `ab_gate_rates.py`
(penetrating pairs, max depth, floating parts) as the primary signal, with the judged score
secondary under a sign test.

Also open, from the same A/A: **7 of 122 recorded static-object runs shipped a plan with
≤ 1 part**, all *after* the plan-budget gate landed.  The gate is not broken —
`plan_quality_complaint` fires on exactly that plan and emits `plan.thin` — but after
`MAX_QUALITY_REASKS` the planner accepts what came back and tells nobody: that pitcher-pump
run carries `status: passed`, `score: 0.75` and no trace of the complaint.  The verdict
should reach the record and the rubric the way `missing_must_acceptance` does.  Explicitly
**not** by failing the run — that was C1.
