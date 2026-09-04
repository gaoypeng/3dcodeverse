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
three do.  The two that keep more than one degree of freedom keep the right ones — the
pram's four wheel spins beside its fold hinge, the pantograph mirror's tilt beside its
extension — so `movable − mimic` counts DOF, not missed couplings.

**The three are the interesting part.**  `nomimic`'s couplings are not the planner's —
they cannot be, the field is gone — they are the AGENT's, written by hand into
`robot.urdf`, whose contract still documents `<mimic>`.  It supplied one for the umbrella,
the venetian blind and the step ladder: the three most textbook one-input mechanisms on
the battery, and nothing else.  So making the coupling a plan field is not teaching the
model a concept it lacks; it is turning something it volunteers only in the obvious cases
into a question it has to answer every time.

**Why the gate rate did not move, answered.**  In the `mimic` arm every sampled pose is
reachable by construction: eight of the ten models have exactly ONE free joint, so their
poses lie on a one-dimensional path, and the other two add a real second freedom (the
pram's wheel spins, the mirror's tilt).  Every overlap the gate reports there is therefore
an overlap the mechanism can actually reach.  Over both arms' 20 runs the gate raised **49
ERROR findings across 47 DISTINCT link pairs** — not one pair repeating — at a median
penetration of **5.8 mm** (max 16.7) against a 1 mm tolerance, and some are already
present at rest: `base_frame|screw_slider` on the scissor lift overlaps in 3 sampled poses,
worst 8.8 mm, one of them the rest pose.

So the coupling support did not reduce gate errors because those errors were not mostly
artefacts of unreachable poses: **the mechanisms genuinely self-intersect**.  The defect
these prompts expose is in generation, not in the sampler and not in the gate — which is
consistent with the scores (median 0.241) and with the judge's leading complaint on the
scissor lift, "the deck does not rise".

One caveat the construction argument does not remove: reachable means reachable in the
KINEMATIC MODEL.  A plan with wrong joint limits lets the model reach poses the real
object could not.
