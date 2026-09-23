# Skills ledger — what each bundle claims, and whether it is earning its place

A skill library you cannot measure per-skill is one you can only maintain by taste. This is
the maintenance surface: **one row per bundle**, naming the single deterministic quantity it
claims to move, the direction, the number it has to beat, and what it owes before it can be
called earned.

**Library: 17 bundles** (13 + the 2026-09-01 scene-graphics port: atmosphere, water,
night, materials — routes R25-R28, evidence `inherited-unverified` from the
scene_multifile_graphics reference ledger). **All 17 route by default since 2026-09-22**
(`C3D_SKILLS` and `C3D_SKILLS_UNVERIFIED` default on; `C3D_SKILLS=0` is the off switch) —
the owner's call, on the live evidence in §0b.  They shipped OFF from 2026-08-25 to
2026-09-22; §5 records why that was the honest default then.  Zero have a measured effect.

Three files hold it up:

| file | what it is |
|---|---|
| `codeverse3d/addons/skill_targets.py` | the table — one `Target` row per bundle |
| `eval/bench/skill_targets.py` | the readout — one command, battery **or** A/B |
| `tests/skills/test_targets.py` | the pins — the rows exist, the gate kinds are still live, the frontmatter agrees, the readout counts what the row says |

```
python eval/bench/skill_targets.py eval/bench/out                     # every baseline, whole corpus
python eval/bench/skill_targets.py eval/bench/out --round first       # where a bundle acts, before repair
python eval/bench/skill_targets.py eval/bench/out/ab_skills           # control vs variant, paired
python eval/bench/skill_targets.py eval/bench/out --skill c3d-part-contact --per-run --json
```

---

## 0a. In flight — the first whole-library pinned A/B (2026-08-26)

`eval/bench/out/fancy_v1/{bl_a,bl_b,cq,tj}`: 16 hard, design-realistic object prompts (blender 10,
cadquery 3, threejs 3), `C3D_SKILLS=1` (the whole library) against OFF, plans pinned, the same
fixed judge, on all 22 keys.  Read it out with `python eval/bench/skill_targets.py eval/bench/out/fancy_v1/<driver>`
per driver, or the merged paired table.  What it can and cannot say, stated before the data
lands: n ≤ 16 pairs against a judged-score floor of 0.202 resolves a ~0.2 mean move, not a
per-bundle effect (§5); its value is the first paired data on the library *as shipped* and the
first-round gate rates (`--round first`) where the bundles act.  Gemini spent the night in a
503 storm; pairs recorded `infra_failed` / `error` / `budget`-with-no-judged-round are outages
(EVAL §7) and are re-run with `--redo-status`, never scored.

**Final readout, 12:50 (wave finished; 13 pairs, plan-pinned, ON − OFF, fixed pro judge):**
microscope +0.087, carousel −0.109, chamber_organ 0.000, harp +0.012, lamp −0.362 (−0.242
re-aggregated), orbital_shaker −0.211, lever_espresso −0.575, ships_wheel −0.256, gate_valve
−0.058, turbocharger +0.331 (control 0.000 was the evaluator's cadquery build defect, a303086 —
not a result; drop it), jukebox −0.326, marimba +0.077, smock_windmill +0.260.  With
turbocharger dropped: n=12, mean **−0.122** (re-aggregated −0.112), sd 0.24, sign 4/12 —
inside the 0.202 floor on the mean, with the large negatives being real defects in the ON runs
(a lever espresso that fell apart, a lamp and a wheel that lost must-items).  The whole
library, measured as shipped, does not help flash on hard objects and may hurt; **skills stay
OFF**.  Storm cost of this wave: 16 pairs took 12 h of wall clock across four drivers for 13
real pairs; the redo machinery (RUNBOOK 7.x/7.w) is what produced the last seven.

**07:00 readout (5 real pairs, plan-pinned, ON − OFF):** harp +0.012, microscope +0.087,
lamp −0.362, ships_wheel −0.256, bubbler_jukebox −0.326; mean −0.169, sd 0.204, sign 2/5.
Three ON cells landed on exactly 0.600 — what a cap looks like from the outside.  Reading
the raw judgments: the lamp's cap was a false `floating_part` on a passed connectivity gate
(fixed in scoring, EVAL §6; re-aggregated 0.720 → pair −0.242), the ships_wheel's is a
`missing_must_acceptance` cap (a must-item the ON run really lacks), the jukebox's is
interpenetration (15–18 mm WARNs) plus flat materials.  With the lamp re-aggregated: mean
−0.145, sd 0.182 — still inside the 0.202 floor, still 2/5 on sign; nothing here says the
library helps, and the two large negatives are real defects in the ON runs, not judge noise.
Skills stay OFF.  Pairs still in flight (carousel_horse, lever_espresso, smock_windmill,
gate_valve, turbocharger, marimba, jacobs_chuck redo) land in `results.jsonl` per driver;
merge with `python eval/bench/skill_targets.py` or the paired readout (scratch `fancy_readout.py`
— rows are only paired when both arms are `scored`).

## 0b. 2026-09-22 — all four vendor CLIs load the bundles; default ON

**Question.**  With `api-agent` gone (2026-08-28), does each vendor CLI the harness drives
actually see the routed bundles and load them — and can we tell from the CLI's own record
instead of from atime?

**Rig** (scratch, not a battery).  One workspace per CLI, built through the harness's own
code path: `Workspace.create` → `spec.json` (static_object / blender) →
`agents.materialize.materialize_workspace` → `skills.attach_skills(kind="baseline")` with a
chair plan (4 parts, 4 mirrored legs, 3 slats).  The router picked **four** bundles —
`c3d-blender-forms` (R8), `c3d-part-contact` (R1), `c3d-bbox-contract` (R3),
`c3d-repeats-and-mirrors` (R6) — and materialised the never-routed control
`zz-c3d-read-control` beside them in both roots.  Each session was launched by
`get_coding_agent("<kind>:<model>").run(AgentJob(..., kind="baseline", spatial_tools=True))`
— the metered backend and the argv/env/settings builders the round loop uses — with the
blender `system.md` as `system_append` and a chair brief that never mentions skills (the
only skills text in the session is the harness's one mandate sentence in the body file).
Workspaces under an untrusted `/tmp` path on purpose.  Reads come from the tool trace each
backend now writes (`agents/cli_common.record_tool_calls`); the atime probe was run on the
same workspace afterwards for comparison.

**Static checks first, no model call:** `gemini skills list` under the harness's per-session
system settings listed the four + the control, all `[Enabled]`, from
`<ws>/.agents/skills`; `codex debug prompt-input` in an untrusted workspace rendered a
`## Skills` section with the four + the control as `r1/<name>/SKILL.md`, `r1` =
`<ws>/.agents/skills` (plus codex's five `.system` skills).

| CLI · model | exit | index the model was shown | activated (surfaced) | refs (deep) | control | atime probe, same session | cost |
|---|---|---|---|---|---|---|---|
| gemini-cli 0.53.0 · gemini-3.7-flash, session 1 | timeout 1 202 s — a `fetch failed` / 503 storm inside the CLI's retry loop | the 4 + control (`gemini skills list`) | **4 / 4** — `activate_skill` ×4 in its FIRST turn | 0 | untouched | 4/4 deep, control "read" (blind) | harness $0 (no envelope); its chat record: 1.19 M prompt (0.92 M cached), 19.0 k out+thoughts ≈ **$0.34** |
| gemini-cli 0.53.0 · gemini-3.7-flash, session 2 | timeout 902 s — same storm | same | **4 / 4** — `activate_skill` ×4 in its first turn | 0 | untouched | 4/4 deep, control "read" (blind) | harness $0; chat record ≈ **$0.34** |
| claude-code 2.1.280 · sonnet (served claude-sonnet-5) | completed, 294 s, 25 turns, 20 calls | `init.skills`: the 4 + control (+ 26 bundled / user-level) | **4 / 4** — `Skill` ×4, its first four calls | 0 | untouched | 4/4 deep, control "read" (blind) | $0.955 reported (subscription) |
| codex 0.155.1 · gpt-5.6-luna (effort high) | completed, 545 s, 29 calls | `## Skills`: the 4 + control (+ 5 `.system`) | **4 / 4** — `sed` of each `SKILL.md`; its first `sed` mis-expanded `r1` as `r0`, exited 2 and is NOT credited (`failed`) | 0 | untouched | 4/4 deep, control "read" (blind) | ≈ $0.085 at API prices (subscription) |
| agy 1.2.2 · gemini-3.7-flash-medium | completed, 102 s, 22 calls | its first four calls were the four absolute `SKILL.md` paths, no listing before them | **4 / 4** — `view_file` ×4 | 0 | untouched | 4/4 deep, control "read" (blind) | $0 (subscription) |

**Read-out.**
* **Every CLI loaded every routed bundle, unprompted, and never the control** — 20 of 20
  activations across five sessions, all before the agent wrote a line of code.  codex said
  so in its first message ("I'm using the chair-relevant 3D skills: Blender form construction,
  bbox/contract control, part-contact checks, and mirrored/repeated-part handling").
* **No session opened a single `references/` file.**  The depth tier is unused on a baseline;
  the old "deep" 100%s were git (§4 of docs/SKILLS.md).  This is now a measured 0, not an
  unmeasured one — the first honest reading of the design's depth metric.
* **The atime probe was wrong on every session** and its control said so each time — the
  reason the probe now reads transcripts first.
* The two gemini sessions were killed by a provider storm, not by skills; both still left
  their tool trace (found by `.project_root` + mtime, not `session_id`, for exactly this
  case).  They also expose a cost gap outside this lane: a killed gemini-cli session is
  recorded at $0 although its chat record holds ~$0.34 of tokens (closed the same day:
  the chat record is such a session's usage — `agents/backends.read_gemini_chats`).
* codex's MCP server did not start in this rig (codex hands MCP servers a scrubbed env; the
  lane worktree is on `PYTHONPATH`, not installed), so it spent its session probing the
  server by hand — a rig artefact, not a skills result.

**Gaps closed** (code, 2026-09-22): gemini-cli `skills.enabled` pinned in the per-session
system settings (merged last; a user's `false` would have hidden every bundle); claude-code
switched to `--output-format stream-json --verbose` (its `json` output carried no tool
calls); every backend writes its CLI's tool calls into `transcript.jsonl` and the read probe
reads them first; agy's `AgentResult.tool_calls` is the trace count (was always 0).
**Verified not to be gaps:** gemini-cli folder trust (off ⇒ trusted ⇒ both workspace roots
load; ON would skip them), codex trust (none needed), agy's `--disable-slash-commands` (only
`/name` prompt expansion), claude-code's `Skill` in `--allowedTools` (present since 2026-08-25).

**Decision:** `C3D_SKILLS` and `C3D_SKILLS_UNVERIFIED` default ON (docs/SKILLS.md §6, the
2026-09-22 note).  An A/B of skills now names its no-skills arm explicitly:
`eval/bench/ab_plan.py --variant-env C3D_SKILLS=0` (ab_plan's control inherits the driver's
env minus the variant keys, so `C3D_SKILLS=1` would be two identical arms).

## 0. What this wave decided (2026-08-25, curate)

Three measurement waves ran: a description A/B read out on **read rate** (ground truth from
api-agent's `read_skill` tool calls), and three independent **effect** A/Bs on the bundles'
own target metrics. The results, stated plainly:

* **No bundle's target moved, separated from that metric's own A/A floor.** Not one. Every
  A/B came back `underpowered`, and for several the underpowering is structural rather than
  a matter of n. **So nothing was promoted to `measured`, and nothing ships on.**
* **One bundle was cut**: `c3d-form-manifest`, read 2 of 19 times before *and* after its
  description was rewritten. The numbers are in this ledger; the bundle's text is in git history
  (`docs/skills-attic/`, removed 2026-09-21).
* **Four bundles were revised by fixing their delivery, not their prose.** The scene track
  never handed its skills to the sessions that do the work. That is now fixed (§3).
* **`cadquery-forms` and `threejs-forms` stay `inherited-unverified`.** Their languages have
  **4 (cadquery) and 3 (threejs) graded runs**, against the 20 the rule requires. Neither
  moved this wave.

### Two words this ledger keeps apart

`metadata.evidence` is about **provenance** — where the bundle's prose came from. A row can
say `measured` because its defect descriptions were derived from our corpus, and still have
**no measured effect**. Those are different axes and this wave separated them everywhere:
5 bundles are `measured` for provenance; **0** are measured for effect.

---

## 1. Why these are gate numbers and not scores — and why that was not enough

An 8-prompt A/A of `eval/bench/ab_plan.py` put the paired sd of the judged score at **0.202** and
printed *~408 paired prompts to resolve +0.02* (`eval/docs/EVAL.md` §8). Every row below is read
from a gate report, a `BuildResult`, the exported GLB or the sampled frames — never the judge.

The premise this wave was built on was that **pinning the plan** would collapse the noise,
because `eval/docs/EVAL.md` §8.1 showed the variance is the planner's. Pinning was built, wired
and verified in production (one planner call per pair, both arms cache-HIT on the same
`inputs_hash`). **The premise was half wrong, and that is the wave's most useful finding.**

* Regressing `contract_findings` on `n_parts` over 198 runs gives **R² = 0.103**. Removing
  the plan-size term moves paired sd only 2.49 → 2.36. Among the 44 runs that all planned
  8 parts, sd is still 2.02. **The spread is generation noise at fixed plan size.**
* Measured directly: a pinned A/A pair scored **0.450 vs 0.654** on identical, plan-pinned
  arms — a 0.204 spread, indistinguishable from the 0.202 measured *without* pinning.
* A shader plan has no parts, so pinning removes almost nothing on the graphics track.

Pinning is still correct and still **cheaper** (one planner call per pair, not two). It is
just not the lever. Measured A/A floors on the target metrics themselves:

```
metric                      A/A delta (identical arms)  paired sd    ±2SE   n to resolve 25%
interpenetrating_pairs                        +5.667       8.963   10.349        483
feature_density                             +3904.7      3057.6   3530.6          92   [bundle cut]
contract_findings                             -2.000       2.646    3.055        449
mean_edge_density (pinned)                    +0.069      0.1275   0.1804        123
```

`±2SE` on `interpenetrating_pairs` is **3.2× the entire corpus mean it is meant to measure**.

**The single highest-leverage statistical fix, unspent:** read these metrics as a **hit
rate**, not a mean count. `P(run has ≥1 contract finding)` = 63%; halving *that* needs ~24
pairs instead of 449. The counts are spiky (38–83% zeros, max 10–13), so a mean ± 2SE throws
away most of the signal. 24 pairs is affordable; 449 is not.

---

## 2. The ledger

Baselines are recomputed 2026-08-25 from every run under `eval/bench/out` that a gate reported on
(`python eval/bench/skill_targets.py eval/bench/out`). Both rounds are shown where they differ, because
**several bundles act on the first round and the repair loop hides them by the last.**

Graded runs per language (a run is a sample **iff** it has a round carrying a gate report):
**blender 145, urdf_blender 25, glsl_shader 17, scene_threejs 9, opengl_python 6,
cadquery 4, threejs 3.** The corpus is live — sibling batteries land runs during a wave —
so every number below carries the snapshot it was read from.

| bundle | evidence (provenance) | target metric | dir | baseline — last round | first round | graded n | effect | status |
|---|---|---|---|---|---|---|---|---|
| **c3d-part-contact** | measured | `interpenetrating_pairs` | down | **3.25** mean / 2.0 med, n=177; 71 at 0 | 4.91 / 3.0 | 145+25 | none — underpowered (needs ~483 pairs) | **best live candidate.** Routed, delivered, read 55%. Blocked only by the noise floor. Needs a *rate* metric (§5) |
| **c3d-bbox-contract** | measured | `contract_findings` | down | **1.47** mean / 1.0 med, n=186; 72 at 0 | 1.20 / 1.0 | 145+25+9 | none — underpowered (~449 pairs) | most-listed and best-read bundle (68%). Its A/B produced 0 usable pairs: the variant hit its budget cap with **zero gated rounds** while still being judged (§4) |
| **c3d-repeats-and-mirrors** | measured | `contract_instance_findings` | down | **0.37** mean / 0.0 med, n=177; **142 of 177 at 0** | 0.32 / 0.0 | 145+25 | none — 1 pinned A/A pair, tied | **floor effect.** Read 38%, the lowest of any delivered bundle. Needs an instanced-prompt slice *and* a read-rate re-check |
| **c3d-blender-forms** | measured | `blender_lint_findings` | down | **0.87** mean / 0.0 med, n=170; **137 of 170 at 0** | 0.60 / 0.0 | 145+25 | none — structural (~230 pairs to halve) | 81% of runs are already clean and sd (2.31) is 2.7× the mean. The metric also cannot see the form-to-technique table, which is half the bundle |
| **c3d-urdf-joints** | measured | `joint_sweep_errors` | down | **6.96** mean / 0.0 med, n=25; 21 at 0, worst 101 | 9.72 / 0.0 | 25 | none — **~1420 pairs**, not bought | heavily tailed: three runs carry every error. Do not A/B this metric. Read 4/4 |
| **c3d-glsl-craft** | mixed | `mean_edge_density` | up | **0.221** mean / 0.186 med, n=17 (0.031–0.464) | 0.220 / 0.192 | 17 | none — pinned A/A gave ±0.180 on identical arms | corpus nearly doubled (9→17). Still needs ~123 pairs; **its own A/A is the evidence against this metric** |
| **c3d-opengl-pipeline** | mixed | `gl_frame_findings` | down | **0.167** mean, n=6; 5 of 6 at 0 | same | 6 | none — floor effect | one finding in the whole corpus. Cannot go down much. Read 4/4 |
| **c3d-scene-lighting** | mixed | `dark_or_flat_frames` | down | **2.89** mean / 3.0 med, n=9 | **4.89 / 4.0 — 0 of 9 runs clean** | 9 | **never delivered until now** (§3) | **revised.** The largest untouched headroom in the library, in the exact stage that was skipped. Gate semantics changed today (§4) — re-baseline before pairing |
| **c3d-scene-composition** | mixed | `camera_placement_findings` | down | **0.56** mean, n=9; 7 at 0 | 0.44 / 0.0 | 9 | **never delivered until now** (§3) | **revised.** But the metric **under-counts by ~2×** — see §4. True first-round rate is 1.44/run, not 0.44 |
| **c3d-scene-motion** | mixed | `min_authored_changed_frac` | up | 0.038 mean / 0.027 med, n=9 | 0.024 / 0.019 | 9 | **UNMEASURABLE** (`measurable=false`) — see §6 | **revised (delivery).** Kept: its read demand has never been measurable either, so the cut rule's *and* is not satisfied |
| **c3d-threejs-shader-traps** | mixed | `shader_preflight_findings` | down | **0.00**, n=9 | 0.00, n=9 | 9 | **NOT INSTRUMENTED** — see §4 | **revised (delivery).** Every listing it has ever had was in a scene session that was never offered the read tool, so it is *unmeasured*, not unread |
| **c3d-cadquery-forms** | inherited-unverified | `build_failure_rate` | down | 0.00, n=4 (all 4 built) | same | **4** | no variance to move | **routed OFF.** Needs 20 graded cadquery runs; has **4**. Read 2/2 when forced on |
| **c3d-threejs-forms** | inherited-unverified | `missing_parts` | down | 0.00, n=3 | same | **3** | no variance to move | **routed OFF.** Needs 20 graded threejs runs; has 3. Read 2/2 when forced on |

**Cut this wave:** `c3d-form-manifest` (text in git history; `docs/skills-attic/` was removed 2026-09-21).

### Read rate — the cheap filter, and the only thing that resolved

Ground truth is api-agent's `read_skill` tool calls, extracted from trajectories. The atime
probe behind `3dcode skills report` **cannot** measure this and its own control says so: the
control bundle came back "opened" in 27 of 33 sessions.

| bundle | opened / listed (pooled) | 95% CI |
|---|---|---|
| `c3d-cadquery-forms`, `c3d-threejs-forms`, `c3d-urdf-joints`, `c3d-glsl-craft`, `c3d-opengl-pipeline` | 4/4 – 5/5 = **100%** | wide; 1-skill sets flatter |
| `c3d-bbox-contract` | 13/19 = **68%** | [46%, 85%] |
| `c3d-blender-forms` | 6/10 = **60%** | — |
| `c3d-part-contact` | 6/11 = **55%** | [28%, 79%] |
| `c3d-repeats-and-mirrors` | 3/8 = **38%** | [14%, 69%] |
| ~~`c3d-form-manifest`~~ | 2/19 = **11%** | **[3%, 31%] — excludes every other bundle. Cut.** |
| the 4 scene bundles | 0/30 — **never delivered**, see §3 | not a read failure |

Overall, restricted to sessions actually offered the tool: **58% (51/88)** against the
design's 80% target. **Confound to respect:** read rate tracks how many bundles are listed —
every 1-skill session read its bundle 4/4 while 5-skill sessions averaged ~55%. Any future
wording A/B must hold the routed set size fixed or it measures the cap, not the copy.

---

## 3. The revision this wave shipped: the scene track never delivered its skills

The read loop saw `c3d-scene-composition` / `-lighting` / `-motion` / `-threejs-shader-traps`
at **0 opens across 30 listings** and revised their descriptions. That was the wrong fix, and
two effect owners independently found the right one in the code:

* `SceneTrack.prepare()` (the pre-round hook then; the declared `stages` since 2026-09-22)
  generated the **entire** scene baseline — `_env_stage`, `_zones_stage`, `_assemble_stage`
  each called `tracks.generation.generate` directly.
* `skills_hook.attach_for_round` was reached from **exactly one place**, `steps.run_round`.
* `SceneTrack.baseline_tasks` returns `[]`, so round 0 listed the bundles to a round with no
  generation task to consume them — which is precisely "listed 3, opened 0".
* The router had `kinds=("env", "zone", "compose")` rows for these bundles all along
  (registry R14–R21). **They were selected for the stages that need them and delivered to
  nobody.** `c3d-scene-lighting` routes for `kind="env"` — the stage where a scene's
  lighting is authored.

**Fixed.** `_env_stage` / `_zones_stage` / `_assemble_stage` now go through
`_deliver_skills` (attach + inline) and `_record_skills` (telemetry), attaching with their
own kind so the router's rows fire as written. The zone fan-out attaches **once**, outside
the worker, because three parallel sessions share one workspace and would race the same
`AGENTS.md`. The prompt manifest's scene scenarios (`tests/prompts/manifest.py`) now pin
each stage's `skills.attached` event and the bundles it routed (the dedicated
`test_delivery_reaches_the_session.py` went in the 2026-09-23 test cut).
(Since D70 the zones stage is ONE session; the fan-out arm was removed 2026-09-21 and the
test now pins one attach before that one session.  Since 2026-09-22 env and zones share one
wrapper, `SceneTrack._stage_session`, in place of `_deliver_skills` / `_record_skills`.)

**What I expect it to do.** `dark_or_flat_frames` is **4.38 per run on the first gated
round with 0 of 8 runs clean** — the largest untouched headroom in the library, sitting in
the one stage that was being skipped. If `c3d-scene-lighting` does anything at all, this is
where it shows up. **This is a prediction, not a result** — no scene run has yet been made
with delivery working. The read-rate re-measurement is the next wave's first job, and until
it lands these four rows stay `mixed`, not `measured`.

---

## 4. Instrumentation gaps — nine, and five would have produced confident wrong answers

* **`camera_placement_findings` sees less than half its own defect class.** The classifier
  matches the *message*, and `scene_frames` emits four camera faults under three wordings.
  Counted today: `camera_in_geometry` (17 findings) and `camera_underground` (3). **Not
  counted: `camera_below_high_ground` (21) and `camera_low` (3)** — both plainly camera
  placement, both invisible to `c3d-scene-composition`'s target. True first-round rate is
  **1.44 findings/run against the 0.44 the row reports**, and 5 of 9 runs that read as clean
  are not. The gate already emits a structured `data["kind"]`; `finding_kind` regexes the
  prose instead. **Not fixed here on purpose** — widening the classifier also widens
  *routing*, which is an effect claim needing its own evidence (same reason
  `scene_frames/content_*` is left alone). Fix it as its own change, with a re-baseline.
* **The `dark_or_flat` gate changed semantics today** (`fcf2afe`, "a night scene is not a
  broken render"). A dim frame with real contrast (`lum_std ≥ LOWKEY_MIN_STD`) is now a WARN
  reading *"frame is dim but lit … low-key by design"*, and **that message classifies as
  `None`** — so it counts nowhere. No historical run carries it, so the baselines above are
  intact; but any A/B straddling this commit compares two different metrics.
  `c3d-scene-lighting` must be re-baselined against the new gate before it is paired.
* **`shader_preflight` is not a gate at all.** It appears in **zero** `record.json` files
  corpus-wide: `check_shaders` is an agent-invocable tool (`spatial/tools.py`), never a
  pipeline gate, so no round appends its report. The only evidence is 8 per-run
  `artifacts/shader_preflight.json` files, all clean. "Not instrumented" and "measured,
  always clean" must not print the same number — the readout now returns `None` when the
  artefact is absent, so the row's denominator is 8 and not 198. Rule of three puts the 95%
  upper bound on the base rate at 0.43/run. **Since 2026-09-22** every scene round carries
  it (`BuildResult.gates`, appended by the round built or not); runs before that date still
  do not.
* **A run is a sample iff it has a round carrying a gate report.** `RunRecord.status` says
  how the round loop *ended* (then `passed`/`plateau`/`budget`/`failed`; since 2026-09-22
  only the stop reason, `max_rounds`/`budget`/…), not whether gates ran.
  Filtering on status dropped every `budget` run — about half the corpus — and would have
  admitted a `passed` run with no gated round as a clean 0.
* **A budget cap tuned for the judged score starves the gate readout.** The `bbox-contract`
  A/B's variant produced a *scored* artefact with **zero gated rounds**, so the primary
  metric lost the cell while the secondary kept it. Raise `--max-minutes` for
  any A/B whose readout is a gate.
* **The A/B rig scored four of seven languages 0.0.** `compare_backends._run_harness` gated
  the harness arm on `src/model.py`; a glsl run that finished `passed` having written
  `src/shader.frag` was recorded `no_code`. Both arms would have scored 0.0 and the rig would
  have reported "no effect" for a switch it never tested. **Fixed** (`entry_of(spec)` reads
  `contracts.common.ENTRY_FILE`). This is why no graphics or three.js bundle has ever had a
  readable A/B.
* **`eval/bench/_fixed_eval.FixedEvaluator` was blender-only.** It pinned `get_runtime(BLENDER)`
  and ran only lint and connectivity, so the *judged* column of any non-blender A/B was
  meaningless. **Fixed** 2026-08-26: each cell builds with its own language's runtime
  (`eval/tests/test_fixed_eval_language.py`); articulated cells add the joint sweep, graphics
  cells their frame metrics, scene cells (2026-09-07) the `scene_frames` gate — contract still
  never runs (it has no plan). It never sank the primary readout either:
  `eval/bench/skill_targets.py` reads each arm's own harness record. (`docs/SKILLS.md` §9 points
  here.)
* **`telemetry.record_exact_read` is wired to `read_file` only** (`agents/api_agent.py`), so
  `read_skill` — api-agent's actual read channel — never reaches `skill_reads.jsonl`.
  *(Moot since 2026-08-28: the api-agent and its `read_skill` channel were deleted with it;
  vendor-CLI reads go through the filesystem and leave no exact-read channel to wire.)*
* **`scene_frames/content_*` has no classified kind.** Frame coverage is half of what
  `c3d-scene-composition` teaches; it neither routes nor counts. Left alone deliberately:
  adding a kind widens routing, which is an effect claim needing its own evidence.

---

## 5. What ships, and why the answer was "nothing" (2026-08-25; superseded 2026-09-22, §0b)

The library does **not** have to share one switch. `C3D_SKILLS_ONLY` restricts the library
*before* routing, so shipping a subset is one env var:

```
C3D_SKILLS=1 C3D_SKILLS_ONLY=c3d-part-contact,c3d-bbox-contract   # the vehicle
```

**Today that list is empty, and all 17 bundles default OFF.** Not for lack of a mechanism,
and not because the wave ran out of time — because **zero bundles have a measured effect**,
and every candidate default-on is a token cost per session against an unmeasured benefit.

Read rate is a *leading* indicator, not an outcome. `c3d-bbox-contract` being opened 68% of
the time is evidence it is discoverable; it is not evidence it helps.

**What flips a bundle to default-on:** a pinned A/B on a metric with enough headroom to
resolve at an affordable n — which today means a **hit-rate** readout (§1) rather than a mean
count — moving in the stated direction with more prompts better than worse. `24` pairs is the
realistic bar for `contract_findings`; `449` is what the mean costs. Get the metric right
first, then buy the pairs.

### When a bundle is cut

A bundle is cut when **any** of these holds, and cutting is a win for the wave:

1. it is **unread after a revision** — `c3d-form-manifest`, 2/19 pooled, CI [3%, 31%];
2. its target did not move and it has **no understood failure mode**;
3. it has **no honest deterministic proxy AND no read demand**.

`c3d-scene-motion` satisfies the first half of (3) and **not** the second: it has never been
delivered, so its read demand has never been measurable. It is kept, and it is now delivered.
The distinction matters — cutting on an instrument that was never pointed at the thing is how
a library loses the bundles that were fine.

---

## 6. The one bundle with no honest proxy

`c3d-scene-motion` is marked `measurable=false`, and its own body is the evidence.

* The deterministic instrument is `spatial/frame_motion.py`: a camera counts as moving when
  ≥0.4% of its pixels change between t=0 s and t=1.5 s.
* `scene_moves` is an **any**, not an **all**: one authored camera over the bar clears the
  gate for the whole scene. `scn_easy_desert_canyon` ships an establishing shot frozen at
  **0.13%** in both rounds and passes anyway, because two other cameras move.
* The judge scores animation **item by item** — *"the planned animations for water and
  falling leaves are completely static"* — and `animation_life` averages **0.431, the lowest
  criterion in the corpus**.

No deterministic instrument attributes motion to a *named plan item*, so any number here
would be a proxy invented to fill the row. `min_authored_changed_frac` is printed as a
**regression guard only**: a drop below the 0.4% bar is a fault the gate would fire on.
Raising it is not evidence the bundle worked.

Building the honest instrument — mask the plan's animated objects, diff only those pixels —
is a harness change, not a skill change.
