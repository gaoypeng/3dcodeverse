# `coupled_v1`, two arms: who decides that a mechanism has one input

Ten one-input linkages (`bench/prompts/coupled_v1.yaml`), baseline round only
(`--rounds 0`), one arm per tree, run one after the other on 2026-09-04.

* **mimic** — the branch as it ships: `JointPlan.mimic` exists, the plan prompt asks a
  one-input mechanism to declare its coupling, the skeleton emits `<mimic>`.
* **nomimic** — the same tree with two edits and nothing else: the `mimic` field typed to
  `None` so the planner cannot fill it, and the paragraph that asks for it removed from
  `codeverse/prompts/tracks/plan_articulated.j2`.  That tree predates the `bench run`
  change that reports an all-unjudged run as an error, which is why `base_nomimic.jsonl`'s
  umbrella row reads `score_final: null, status: plateau, errors: ""` — the lost pair
  behind "9 paired prompts" below.

    python bench/coupling_stats.py <mimic_dir> <nomimic_dir> --per-prompt
    python bench/paired_compare.py <mimic_dir> --against <nomimic_dir>

| readout | mimic | nomimic |
|---|--:|--:|
| prompts whose URDF declares a coupling | **10 / 10** | **3 / 10** |
| URDFs left with a single degree of freedom | 8 / 10 | 3 / 10 |
| movable joints (median) → driven per sampled pose | 6 → 1 | 7 → 1 (of the three) |
| `joint_sweep` rounds failed | 5 / 10 | 4 / 10 |
| `joint_sweep` ERROR findings per round | 2.60 | 2.30 |
| fixed-judge score, 9 paired prompts | 0.248 | 0.254 |
| paired Δ | \-0.006, 95 % CI [\-0.193, +0.181], 4/5/0, sign p = 1.000 | |
| cost | \$23.48 | \$18.90 |

**The score says nothing, as designed.**  Nine pairs against an A/A band of ±0.13 cannot
resolve anything smaller than a landslide, and the per-prompt deltas (−0.446 … +0.404)
have exactly the shape of noise.  The battery was run for the mechanical readout.

**The mechanical difference is structural**: asked for the coupling, the planner declares
it every time and eight of ten mechanisms come out with one degree of freedom; not asked,
three do.

**The three are the interesting part.**  `nomimic`'s couplings are not the planner's —
they cannot be, the field is gone — they are the AGENT's, written by hand into
`robot.urdf`, whose contract still documents `<mimic>`.  It supplied one for the umbrella,
the venetian blind and the step ladder: the three most textbook one-input mechanisms on
the battery, and nothing else.  So making the coupling a plan field is not teaching the
model a concept it lacks; it is turning something it volunteers only in the obvious cases
into a question it has to answer every time.

**What this does not show.**  Whether the poses the sampler now avoids were ever the
reason a mechanism scored badly: the `joint_sweep` gate's failure rate is not detectably
different between the arms at n = 10 (5/10 vs 4/10, Wilson 0.24–0.76 vs 0.17–0.69; 26 vs
23 ERROR findings) — which is not the same as "the same rate".  The gate finds overlaps
in reachable poses just as it found them in unreachable ones.  The rows in this directory
carry score / cost / status only; the mechanical counts above were read from the two arms'
`artifacts/robot.urdf` files, which are not in the tree.

**Caveat (2026-09-04).**  Both arms were rendered before the fix in
`spatial/joints_export.robot_scene`: the per-pose GLBs behind the articulation sheet posed
every `<mimic>` follower at rest, so the judge scored sheets in which the coupled links did
not move.  The declaring / one-input counts come from the URDFs and stand; the score row and
the sweep-gate row need a re-run on the corrected export.
