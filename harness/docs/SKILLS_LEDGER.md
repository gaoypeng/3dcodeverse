# Skills ledger — what each bundle claims, and whether it is earning its place

A skill library you cannot measure per-skill is one you can only maintain by taste. This is
the maintenance surface: **one row per bundle**, naming the single deterministic quantity it
claims to move, the direction, the number it has to beat, and what it owes before it can be
called earned.

Three files hold it up:

| file | what it is |
|---|---|
| `codeverse/skills/targets.py` | the table — one `Target` row per bundle |
| `bench/skill_targets.py` | the readout — one command, battery **or** A/B |
| `tests/skills/test_targets.py` | the pins — the rows exist, the gate kinds are still live, the frontmatter agrees, the readout counts what the row says |

Each bundle carries its own row in `metadata` (`target_metric`, `target_direction`,
`target_unit`, `target_measurable`, `target_baseline`), so the claim travels with the skill
to any CLI that reads the bundle, and `tests/skills/test_freshness.py` fails the day the
metric stops existing.

```
python bench/skill_targets.py bench/out                     # every baseline, whole corpus
python bench/skill_targets.py bench/out/static_v2_flash     # one battery
python bench/skill_targets.py bench/out/ab_skills           # control vs variant, paired
python bench/skill_targets.py bench/out --language blender --round first
python bench/skill_targets.py bench/out --skill cv3d-part-contact --per-run --json
```

---

## 1. Why these are gate numbers and not scores

An 8-prompt A/A of `bench/ab_plan.py` put the paired sd of the judged score at **0.202**,
the 2 SE band at ±0.165, and printed *~408 paired prompts to resolve +0.02*
(`docs/EVAL.md` §8). A skills A/B read out on `score` at n=8 is a coin toss. Every row below
is read from a gate report, a `BuildResult`, the exported GLB or the sampled frames — never
from the judge.

That does **not** make them noiseless. The same A/A directory (`bench/out/plan_loop/C0`,
3 pairs usable after the provider weather) reads out like this:

```
skill                        metric                   n   control   variant     delta  b/w/t      sd   ±2SE   n to resolve 25%
cv3d-part-contact            interpenetrating_pairs   3     5.667    11.333    +5.667  0/2/1   8.963  10.349      160
cv3d-bbox-contract           contract_findings        3     1.000     1.000     0.000  2/1/0   1.732   2.000      192
cv3d-form-manifest           feature_density          3  2095.733  6000.467 +3904.733  3/0/0 3057.62 3530.63      136
```

Two arms that were **identical by construction** moved interpenetrating pairs by +5.7. So:

* the deterministic readout is much cheaper than the judged score but still needs three
  figures of paired prompts *unless the plan is pinned* — and `C0` predates
  `bench/pin_plan.py`, which is exactly the term that swing is;
* `CV3D_SKILLS*` sits in `GENERATION_SIDE_ENV`, so `pin_plan_blockers({"CV3D_SKILLS": "1"})`
  is empty and **a skills A/B is pinnable**. Pin it. Every number in §3 assumes it;
* n=3 makes those sd's indicative, not trustworthy. **The first job of the Measure phase is
  to re-read this block on an A/A with ≥8 usable pairs and a pinned plan**, per metric.

---

## 2. The ledger

Baselines are the **last gated round** of every run under `bench/out` that a gate reported
on, recomputed 2026-08-25 (`python bench/skill_targets.py bench/out`). "graded n" is that
language's graded-run count as `tests/skills/test_corpus.py` counts it: blender 141,
urdf_blender 23, glsl_shader 9, opengl_python 5, scene_threejs 3, cadquery 0, threejs 0.

| bundle | evidence | target metric | dir | baseline (last round) | graded n | status | to graduate |
|---|---|---|---|---|---|---|---|
| **cv3d-part-contact** | measured | `interpenetrating_pairs` | down | **3.35** mean / 2.0 median, n=164 (5.07 first round; 65 runs already at 0) | 164 | **earning — measure it** | a pinned-plan A/B moving the mean down with more prompts better than worse |
| **cv3d-bbox-contract** | measured | `contract_findings` | down | **1.51** mean / 1.0 median, n=167 (1.23 first round) | 167 | **earning — measure it** | same, on the non-instance contract kinds |
| **cv3d-repeats-and-mirrors** | measured | `contract_instance_findings` | down | **0.32** mean / 0.0 median, n=164; **132 of 164 already at 0** | 164 | **probation — thin defect** | a battery slice where the metric is non-zero often enough to have headroom (prompts with `instances > 1`), then an A/B on that slice |
| **cv3d-form-manifest** | measured | `feature_density` | up | **2551** median / 7946 mean, n=164 | 164 | **probation — weak link** | it is a *leading* indicator: r = +0.19 against the judged `geometry_detail` (`docs/COMPLEXITY.md` §2.2, n=44). Show the metric moves AND that the criterion follows, or the row is measuring the wrong thing |
| **cv3d-blender-forms** | measured | `blender_lint_findings` | down | **0.87** mean / 0.0 median, n=164 (blender 1.01, urdf_blender 0.00); **133 of 164 at 0** | 164 | **probation — thin defect** | the lint already catches these, so most runs never had the defect. Either show the A/B lowers the remaining 31, or accept that the bundle's real value is the form table, which this metric does not see |
| **cv3d-urdf-joints** | measured | `joint_sweep_errors` | down | **6.91** mean / 0.0 median ERRORs, n=23; 20 of 23 at 0, worst run 101 | 23 | **earning — measure it** | extremely heavy-tailed: three runs carry every error. Pair on prompts with >1 movable joint or the mean is noise |
| **cv3d-cadquery-forms** | inherited-unverified | `build_failure_rate` | down | **none — n=0** | **0** | **routed OFF; unmeasurable today** | 20 graded cadquery runs. Until then no A/B can be run and the bundle stays out of routing (design §5.2 law 3) |
| **cv3d-threejs-forms** | inherited-unverified | `missing_parts` | down | **none — n=0** | **0** | **routed OFF; unmeasurable today** | 20 graded threejs runs |
| **cv3d-scene-composition** | mixed | `camera_placement_findings` | down | **0.00**, n=3 (0.33 on the first gated round) | 3 | **probation — no headroom** | the repair loop already clears every camera fault by the last round. Read it out on the **first** gated round (`--round first`), where the bundle is supposed to act, and get scene runs past n=3 |
| **cv3d-scene-lighting** | mixed | `dark_or_flat_frames` | down | **1.00** mean / 1.0 median, n=3 (2.67 first round) | 3 | **probation — thin corpus** | real headroom (2 of 3 runs still fail on the last round) but n=3. Needs a scene battery of ≥8 prompts |
| **cv3d-scene-motion** | mixed | `min_authored_changed_frac` | up | 0.006 median / 0.017 mean, n=3 (one run's establishing shot is frozen at 0.0013 and the gate still passes) | 3 | **UNMEASURABLE (`measurable=false`)** | see §4 — nothing deterministic sees this bundle's claim. Do not A/B it on this number |
| **cv3d-threejs-shader-traps** | mixed | `shader_preflight_findings` | down | **0.00**, n=3 — every recorded scene run is clean | 3 | **probation — no headroom** | the gate has never fired here, so today this can only catch a regression. Needs a battery whose plans actually author custom GLSL. Also owes an instrumentation fix: `shader_preflight` is written to `artifacts/shader_preflight.json` and never merged per-round into `record.json`, so only a run's final state is readable |
| **cv3d-glsl-craft** | mixed | `mean_edge_density` | up | **0.167** median / 0.203 mean, n=9 (range 0.031–0.464) | 9 | **earning — measure it** | the widest continuous spread of any row, so it is the cheapest graphics A/B we have. Confirm the judged `visual_richness` follows |
| **cv3d-opengl-pipeline** | mixed | `gl_frame_findings` | down | **0.20** mean / 0.0 median, n=5; 4 of 5 at 0 | 5 | **probation — thin corpus** | n=5 and one finding. The bundle's headline defect (trap 1: a solver that gets five steps) shows up in the judge's words, not yet in a gate |

**Read at a glance:** 4 bundles are ready to A/B on their own number today
(`part-contact`, `bbox-contract`, `urdf-joints`, `glsl-craft`). 7 are on probation for a
thin corpus or a floor-effect. 2 are routed off with zero graded runs. 1 has no honest
deterministic proxy at all.

---

## 3. What "graduate" means

A bundle is **earning** when all four hold:

1. **Routable** — its language has ≥20 graded runs, so `metadata.evidence` may say
   `measured` (the rule `tests/skills/test_corpus.py` already enforces).
2. **Read** — the backend actually opens it. `read_skill` (`agents/api_skills.py`) records
   exactly which bundles were opened; measured 2026-08-25 api-agent opened 2 of 3 routed
   bundles, against 0 of 5 before the tool existed, and the three CLI backends read 5 of 5.
   **A bundle nobody opens cannot have an effect — check the read rate before spending on an
   effect A/B.**
3. **Headroom** — its target metric is non-zero on enough of the battery to move. A row
   where most runs already read 0 fails here, and the fix is a prompt slice, not a bigger n.
4. **Moved** — a pinned-plan A/B (`bench/pin_plan.py`) on ≥ the row's `n to resolve` pairs
   moves the metric in the stated direction, with more prompts better than worse.

A bundle that fails (3) or (4) after a fair A/B **gets cut**. Cutting a skill that does not
earn its tokens is a win for the wave, not a failure — the plan-loop's C1 was reverted and
the whole skills A/B shipped OFF on the same reasoning.

---

## 4. The one bundle with no honest proxy

`cv3d-scene-motion` is marked `measurable=false`, and its own body is the evidence.

* The deterministic instrument is `spatial/frame_motion.py`: a camera counts as moving when
  ≥0.4 % of its pixels change between t=0 s and t=1.5 s, and `scene_frames` emits a
  `no_motion` ERROR when no authored camera does.
* `scene_moves` is an **any**, not an **all**: one authored camera over the bar clears the
  gate for the whole scene. Re-measured 2026-08-25, `scn_easy_desert_canyon` ships an
  establishing shot frozen at **0.13 %** — well under the bar — in both of its rounds, and
  the gate passes anyway because two other cameras move. The bundle's own body reports the
  matching fact from the other side: the *best* authored camera cleared the bar by 5–12× in
  all 8 recorded rounds while `animation_life` averaged **0.431 — the lowest criterion of
  any language in the corpus.**
* The judge's reasons are per item: *"the planned animations for water and falling leaves
  are completely static"*. `tracks/scene.py::judge_context` hands the judge the plan's
  `animation` list verbatim and it is scored **item by item**.

No deterministic instrument attributes motion to a *named plan item*, so any number we could
put here would be a proxy we invented to have one. Raising `min_authored_changed_frac` is not
evidence the bundle worked. It is still printed, and still worth watching: a drop below the
0.4 % bar is a regression the gate would fire on.

Building the honest instrument — per-item motion, by masking the plan's animated objects and
diffing only those pixels — is the way this bundle becomes measurable, and it is a harness
change, not a skill change.

---

## 5. Instrumentation gaps this wave found

* **`gl_frames` classified only 2 of its 7 actionable messages.** `R19`/`R24` say *"the frame
  gate saw no motion or no detail"*, but `registry.py` matched only `frame-to-frame` and
  `visual detail` — a shader that came out **static**, **black**, **blown out**, **NaN** or
  that rendered **no frames** classified as `None` and routed nothing. Widened, with a
  parametrised pin in `tests/skills/test_targets.py`. No baseline moves: the four recorded
  `gl_frames` findings were all flicker/low-detail already.
* **`shader_preflight` never reaches the record.** It is written to
  `artifacts/shader_preflight.json` beside the run, so the readout can only see a run's final
  state, not per-round. It should be merged into `rounds[].gates` like every other gate.
* **`scene_frames/content_*` has no classified kind.** Frame coverage (`content_frac`) is
  half of what `cv3d-scene-composition` teaches and `registry.finding_kind` returns `None`
  for it, so it neither routes nor counts. Deliberately left alone here — adding a kind
  widens routing, which is an effect claim and needs its own evidence.
* **`connectivity` and `contract` carry no `data["kind"]`**, unlike every gate written since.
  `registry.finding_kind()` is the classifier that fills the gap, which is why the readout
  goes through it rather than reading `data` — but the two oldest and busiest gates are the
  ones relying on message regexes.
