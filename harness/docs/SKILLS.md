# Skills

A **skill** is a task-scoped rule sheet the harness attaches to a coding session automatically —
from the track, the language, the round kind, the plan and **the previous round's gate
findings**.  Nobody writes `skills: [...]`.

It is not a recipe library.  No language ships a cookbook (D106 for the scene track, D107 for the
rest; owner, 2026-10-15): interface and language facts live in `contract.md` / `system.md` (one
executable example each), and a skill is a sheet of measured requirements with a few SHORT
optional practices — real API names, numbers only where a gate or the judge measures them, no
code block over ~12 lines.  The 60 iteration-0 argon scenes were the inlined chapters executed
verbatim; that is what a chapter buys.

`SKILL.md` is an open standard ([agentskills.io](https://agentskills.io/specification), Dec 2025),
so the format is not ours to bend — and it is why the bundles reach a session only through its
vendor's native skill loader: claude-code, codex and agy discover them themselves (§7).  The one
backend that needed harness-side injection, the in-process `api-agent`, read 0 of 5 routed
bundles and was deleted 2026-08-28.

`C3D_SKILLS` is **on by default since 2026-09-22** (D81); `C3D_SKILLS=0` is the off switch and an
A/B's no-skills arm.  Knobs: `C3D_SKILLS_MAX` (5 bundles a session), `C3D_SKILLS_UNVERIFIED`
(the six `inherited-unverified` bundles, on), `C3D_SKILLS_ONLY` (restrict the library before
routing), `C3D_SKILLS_DIR` (another library).

---

## 0. How the library is managed

**One library, one policy, thin adapters.**  The bundles under `codeverse3d/skills/<name>/` are
plain `SKILL.md` directories that know nothing about any backend — `cp -r` one into
`~/.claude/skills/` and it works without the harness.  `registry.py` picks the set from typed
inputs.  The one thing that differs between coding agents is the discovery root, in one file,
`skills/prompting.py` (`_BACKEND_ROOTS`, read by `skill_path`; `tests/skills/test_delivery.py`
fails if per-backend knowledge leaks anywhere else):

| backend | discovery root |
|---|---|
| `claude-code` | `.claude/skills` |
| `codex`, `agy` | `.agents/skills` |

Read out of the shipped binaries (2026-09-22) and re-run live on 2026-11-03 (§7):

| CLI | roots (workspace · user) | what must hold | what the model is shown | activation, as its own record logs it |
|---|---|---|---|---|
| `claude-code` 2.1.317 | `.claude/skills` · `~/.claude/skills` (+ synced skills, plugins) | `Skill` in `--allowedTools`; never `--disable-slash-commands` ("Disable all skills") | a name + description list; the stream-json `init` event's `skills` | `Skill {"skill": name}`, then `Read` — or a shell `cat` — of `references/` |
| `codex` 0.166.0 | `.agents/skills` of the working root · `$CODEX_HOME/skills` (+ `.system`) | nothing: listed from an untrusted `/tmp` workspace too (`codex debug prompt-input`) | a `## Skills` section with an aliased path `r1/<name>/SKILL.md` | a shell `sed`/`cat` of `SKILL.md` (an `exec --json` `command_execution` item) |
| `agy` 1.4.12 | `.agents/skills` under the repo root · a customization root under `~/.gemini/` | nothing; `--disable-slash-commands` only stops `/name` expansion of the prompt | a skills section of its system prompt, paths included | `read_file` of `SKILL.md` (`view_file` before 1.4) — a step in its conversation database |

(gemini-cli discovered them too, until its backend went in D103.)

The harness writes the bundles under the discovery root and hands the agent no second index (the
loader's own would list them twice) and no tool; the body file gets one MANDATORY sentence.

**A session sees nothing but the routed bundles** (2026-09-22).  Every CLI lists skills of its
own; each is switched off with the CLI's documented knob (`agents/backends.py`), checked at zero
cost against the real binaries with a local fake API:

| CLI | listed besides the routed bundles | switch | left |
|---|---|---|---|
| `claude-code` | 26: bundled (`update-config`, `code-review`, `loop`, …) and account-synced (`deep-research`, `anthropic-skills:*`, …) | `--setting-sources project` + `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` + `--settings {"skillOverrides": {"design": "off", "doctor": "off"}}` | nothing — and the owner's `~/.claude/settings.json` (effortLevel, hooks, env) no longer reaches a session |
| `codex` | 5 `.system` (imagegen, openai-docs, plugin-creator, skill-creator, skill-installer) | `-c skills.bundled.enabled=false` | any user root (`~/.codex/skills`, `~/.agents/skills`): no switch; none here |
| `agy` | 5 built-ins (agy-customizations, antigravity-guide, generative_ui, migrate-workflows, permissioned-github) | none per session | all 5 |

**Why only native loaders.**  Measured 2026-08-25, same library, workspace and task: codex,
claude-code and agy each read 5 of 5 routed bundles unprompted; `api-agent` read 0 of 5 while
making 52 `read_file` calls — the bundles sat in hidden directories its `list_files` skipped, and
the index was prose in a 2.3 kB system prompt.  Prose is not an affordance.

## 1. The model: progressive disclosure, and why it is also the measurement

| tier | what the agent sees | when | our cost |
|---|---|---|---|
| 1 · index | `name` + `description`, listed by the CLI's own loader | every turn | 41 tokens (our one mandate sentence) |
| 2 · body | the whole `SKILL.md` | on activation | 851–2,498 tokens, once |
| 3 · depth | `references/*.md` | when the agent chooses to go deeper | as read |

The read probe (`skills/telemetry.probe_reads`) reads the CLI's own record first (§4): a bundle
is *surfaced* when a call activated it or named its `SKILL.md`, *deep* when a call named a file
under its `references/` — `SkillsUsage.evidence == "transcript"`.  The atime probe is the
fallback for a session that left no trace: `materialize_skills` writes every file with
`atime == mtime` on an ext4 `relatime` root, so the first read afterwards bumps `atime`
(`SKILL.md` → surfaced, `references/*.md` → deep; a bundle with no `references/` reports
`deep_measurable: false` rather than scoring 0).

**Why atime can only be a fallback** — measured 2026-08-25, both reproduced:

1. `Workspace.changed_files` runs `git add -A -N` + `git diff --numstat` after every session,
   and git reads each untracked file to diff it — every bundle flips to "read" with no agent
   involved (`test_git_diff_alone_trips_the_control`).
2. Every CLI opened `SKILL.md` **and** `references/` of a bundle whose description did not match
   the task, under "do not read any files, do not activate any skill" (§7).

So `materialize.write_control` puts one **never-routed, never-indexed** bundle beside the real
ones.  Nothing should open it; when it comes back opened, `SkillsUsage.control_read` is set, the
session's deep-read rate is **`None`** — not 100 %, not 0 — and `3dcode skills report` says how
many sessions it dropped.  That is the difference between a metric and a number that would have
read 100 % forever.  One job stays with atime under transcript evidence: a reference opened by a
call the trace cannot parse (claude-code's `cd … && cat` loop, 2026-11-03) counts as deep while
the control's `references/` are untouched — nothing scanned references/ wholesale (D115).

---

## 2. The library

**Twenty bundles** (2026-11-03).  `lines` is `SKILL.md` as shipped, `tokens` the body; the caps
are 350 body lines and 2,500 tokens.

| skill | track | language | evidence | lines | tokens | routes |
|---|---|---|---|---|---|---|
| `c3d-part-contact` | object tracks | any | measured | 138 | 1,728 | R1, R2 |
| `c3d-bbox-contract` | object + scene | any | measured | 90 | 1,108 | R3, R4 |
| `c3d-repeats-and-mirrors` | object tracks | any | measured | 130 | 1,545 | R6, R7 |
| `c3d-blender-forms` | any | blender, urdf_blender | measured | 128 | 2,195 | R8, R9 |
| `c3d-urdf-joints` | articulated_object | urdf_blender | measured | 171 | 2,172 | R12, R13 |
| `c3d-scene-composition` | scene | scene_threejs | mixed | 151 | 2,150 | R14, R15, R34 |
| `c3d-scene-lighting` | scene | scene_threejs | mixed | 130 | 1,748 | R16, R17 |
| `c3d-scene-motion` | scene | scene_threejs | mixed | 200 | 2,473 | R18, R19 |
| `c3d-threejs-shader-traps` | scene (+ static on a preflight finding) | threejs both | mixed | 128 | 1,722 | R20, R21 |
| `c3d-glsl-craft` | graphics | glsl_shader | mixed | 124 | 1,613 | R22, R24-glsl |
| `c3d-opengl-pipeline` | graphics | opengl_python | mixed | 121 | 1,696 | R23, R24-opengl |
| `c3d-cadquery-forms` | any | cadquery | inherited-unverified | 107 | 1,731 | R10 |
| `c3d-threejs-forms` | static_object | threejs | inherited-unverified | 143 | 2,151 | R11 |
| `c3d-scene-atmosphere` | scene | scene_threejs | inherited-unverified | 86 | 1,061 | R25 |
| `c3d-scene-water` | scene | scene_threejs | inherited-unverified | 83 | 1,045 | R26 |
| `c3d-scene-night` | scene | scene_threejs | inherited-unverified | 78 | 851 | R27 |
| `c3d-scene-materials` | scene | scene_threejs | inherited-unverified | 117 | 1,484 | R28 |
| `c3d-bpy-scene-placement` | scene | scene_blender | mixed | 148 | 2,498 | R29, R30 |
| `c3d-bpy-scene-lighting` | scene | scene_blender | mixed | 139 | 2,356 | R31, R32 |
| `c3d-bpy-scene-materials` | scene | scene_blender | mixed | 128 | 2,075 | R33 |

All twenty route by default (the `c3d-bpy-scene-*` three since 2026-10-14, D105), capped at 5 a
session.  None has a measured effect: they route because the owner made skills part of the
product and every backend loads them natively, not because an A/B promoted them (§6).  To measure
a subset, `C3D_SKILLS_ONLY` restricts the library before routing, against a `C3D_SKILLS=0` arm.

**Retired:** `c3d-form-manifest` (2026-08-25) — opened 2 of 19 times (11 %, CI [3 %, 31 %]), the
one bundle whose read-rate interval excluded every other's, at ~1,917 tokens a listing.  Its route
id **R5** is never reused: a run record refers to a routing decision by id
(`tests/skills/test_registry.py::RETIRED_RULES`).  The text is in git history.

**Two words kept apart.**  `evidence` is provenance — where the prose came from; a `measured`
bundle can have no measured effect, and five do.  Every bundle also declares the ONE
deterministic quantity it claims to move (`target_metric` / `target_direction` /
`target_baseline` in its frontmatter and `addons/skill_targets.py`;
`python eval/bench/skill_targets.py eval/bench/out` prints them, per battery or paired across an
A/B).  `cadquery-forms` and `threejs-forms` stay `inherited-unverified` — routed, unranked —
until their languages reach the 20 graded runs the rule requires (4 and 3 at the last count).

## 3. Routing

`codeverse3d/skills/registry.py` holds the typed table (34 rows; ids R1–R34, R5 retired, R24
split by language) and `finding_kind()`, which names a finding `<gate>/<data.kind>` — the kind
the gate wrote, never its message; `select()` turns `(track, language, kind, plan signals,
findings)` into a ranked, capped, reasoned set.  Four laws, all tested:

1. **Gate findings outrank everything.**  A row answering a finding sits at priority ≥ 90,
   standing rows at ≤ 80: a repair round spends its budget on what broke — the input no CLI's
   own loader can see, and the reason routing is in code.
2. **The cap is 5** (`C3D_SKILLS_MAX`); it cuts the low-priority tail, never the gate-fired head.
3. **Quiet kinds attach nothing on their own** — `asset`, `asset_fix`, `reference` are short,
   narrow sessions.  A gate-fired row still reaches them (R13/R15/R17/R19/R21 are
   kind-agnostic, R2/R4/R7/R9 are not; both halves pinned in `test_router`).
4. **Unknown input degrades to `[]`, never an exception** — a planner failure must not also
   take out the round's skills.

`test_routing_property.py` runs every finding kind the corpus produces from every track ×
language × round kind over the real library (cap, priority ≥ 90, a reason naming the finding)
and checks that every signal a route requires is one `plan_signals()` sets.  `finding_kind()`
reads no message text (N52, 2026-09-24: the regex it replaced left seven `scene_frames` kinds
unrouted and R19 unreachable).  Every gate writes a `data.kind` — lints from
`contracts.artifacts.LintKind`, connectivity `stray_islands` / `no_ground`, motion_direction
`wrong_direction` / `skipped`, render_console `console_error`, scene_probe `draw_calls` — so R9
answers `lint/part_not_imported`, not every lint line; a finding from before its gate wrote a
kind is `<gate>/untyped`, answered only by a family row; INFO findings have no kind (census, not
defects).  A bundle is added with its rows in the same commit — an unroutable bundle pays the
index and never helps, and a test fails on both halves.

---

## 4. Telemetry

Per round, `RoundRecord.skills` is a `SkillsUsage`: listed, surfaced, read deep, `index_tokens`,
`body_tokens_read`, `evidence` and the control's state.  Each attached body's hash goes into
`record.prompt_hashes` as `skill:<name>`; per session, `telemetry/skills.jsonl` gets one line.

**Ground truth: the CLI's own record** (2026-09-22).  Each backend writes what its CLI says it
called into `trajectories/<label>/transcript.jsonl` — one `tool_call` row per call (arguments cut
to 400 characters a value, the CLI's own failure verdict as `failed`), then one `tool_trace` row
closing the session (`agents/cli_common.record_tool_calls`):

| backend | source | activation looks like |
|---|---|---|
| claude-code | `--output-format stream-json --verbose` (plain `json` carried no tool stream); the `init` event's `skills` list is kept as `skills_index` | `Skill {"skill": name}` |
| codex | the `exec --json` events it streams (`command_execution`, `mcp_tool_call`, `file_change`) | a `sed`/`cat` of `…/<name>/SKILL.md` |
| agy | its conversation database, `<HOME>/.gemini/antigravity-cli/conversations/<id>.db`, read before the session's HOME is deleted — a schema-free walk of each step's protobuf for (call id, tool, JSON args) | `read_file` of `…/<name>/SKILL.md` |

`probe_reads` credits only sessions logged after the attach (`SkillsMaterialized.attached_at`)
and uses the transcript only when **every** such session closed a trace — a partial trace
under-counts exactly as the blind probe over-counts.  Under transcript evidence `control_read`
means the AGENT opened the control: counted by the report and flagged, never dropped as blind.

```
3dcode skills report eval/bench/out/<battery>    # deep-read rate per skill per backend (--json)
3dcode skills validate                           # bundles, overlays, routes
```

Target: ≥ 60 % deep-read, counted only over sessions the control says were measurable.  **A skill
under 20 % over 20 such sessions is merged or deleted** — a library that only grows ends as bloat.
The read rate is the first readout, before any score: the former `read_cookbook` tool was called
by 0 of 16 zone sessions although the prompt named five chapters by title.

**Measured 2026-09-22, from the transcripts** — a harness-built static_object/blender workspace,
four routed bundles + the control, each CLI launched by its own backend, a chair brief that never
mentions skills:

| CLI · model | index the CLI showed | activated | deep | control | atime probe, same session |
|---|---|---|---|---|---|
| claude-code 2.1.280 · sonnet | `init.skills`: the 4 + control (+ 26 of its own) | **4 / 4** (`Skill`) | 0 | untouched | 4/4 deep, control "read" → blind |
| codex 0.155.1 · gpt-5.6-luna | `## Skills`: the 4 + control (+ 5 `.system`) | **4 / 4** (`sed`) | 0 | untouched | same |
| agy 1.2.2 · gemini-3.7-flash-medium | its first four calls were the four `SKILL.md` paths | **4 / 4** (`view_file`) | 0 | untouched | same |

Every CLI read every routed body and no reference; the atime probe called every reference "read"
and the control opened — git, not the agent.  The 2026-08-25 atime figures (5 of 5 deep on every
CLI) are therefore an upper bound.  What is not ambiguous is the behavioural trace: codex's
`src/parts/common.py` came back with `WELD_OVERLAP = 0.001` and "1 mm weld overlap with the seat"
— `c3d-part-contact`'s number, where the prompt corpus of the day said 2–5 mm.

---

## 5. What the tests guarantee

`tests/skills/` is hermetic by default; two `slow` cases build a wheel, the `live` cases drive a
real CLI.

| file | guarantees |
|---|---|
| `test_loader.py` | the spec's field rules, as the loader enforces them |
| `test_spec_compliance.py` | the same rules re-derived from the raw bytes, plus the reference validator (`pip install skills-ref` → the `agentskills` CLI) agreeing field by field with our loader |
| `test_library.py` | body budget, no code fence over 12 lines (D107), no restating `conventions.py`, every `_claims` number matches its live constant, no two skills point one claim key at different numbers |
| `test_freshness.py` | every tool, gate kind, rubric criterion, constant, switch and sibling skill a bundle names still exists; every function it tells the agent to call is defined; no bundle points at a cookbook |
| `test_router.py` / `test_routing_property.py` | the four routing laws by row; every corpus finding kind from every session |
| `test_telemetry.py` | the read probe, the control that catches git, the atime complement under the control |
| `test_packaging.py` | a built wheel contains all 20 `SKILL.md`, their `references/`, every `_claims` file |
| `test_live_discovery.py` | §7 — a real CLI finds and opens a bundle |

Two contradiction checks answer different questions: **value-level**, library-wide — two skills
may not point one claim key at different numbers, compared on the pre-scale value ("1 cm" and
"0.01" metres are both right); **text-level**, co-routing only — two skills that can land in one
session and chose the same units must read the same.

---

## 6. Does it help?  The A/B

**Default ON since 2026-09-22** (owner's call, D81).  The 2026-08-25 verdict had been OFF, for
one reason that is gone — `api-agent`, then the bench's generator, read 0 of 5 bundles with
ground truth (52 `read_file` calls, none on a skill path) and was deleted 2026-08-28 — and one
that is now the A/B's job: every CLI backend activates 4 of 4 routed bundles unprompted (§4),
and an *effect* cannot be shown at n = 8 on this rig at all (below).  What ON costs: the
41-token mandate sentence plus the bodies the CLI activates — all of them, up to the cap of five
at 851–2,498 tokens each.  An A/B names its no-skills arm explicitly:
`eval/bench/ab_plan.py --variant-env C3D_SKILLS=0` (ab_plan's control is the driver's env minus
the variant's keys, so the OFF arm must be the variant; its delta reads OFF − ON).

**The rig** (2026-08-25): `ab_plan.py`, arms differing in `C3D_SKILLS` only, `static_objects_v2`,
`--rounds 1`, paired, judge `gemini:gemini-3.1-pro-preview` at `n_samples 2`; the eight prompts
are the ones `eval/bench/out/plan_loop/C0` used for its A/A.  The switch is generation-side (the
planner runs in the `plan` stage before anything is attached), so plan-once-write-both is valid
for it (`eval/docs/EVAL.md` §8.1).

**The noise floor, measured first** — C0, two identical arms, the same eight prompts:

| | |
|---|---|
| paired prompts scored | 6 of 8 (2 `infra_failed`) |
| mean delta | **+0.043** from nothing; paired sd 0.202, SE 0.082, 2 SE band **±0.165** |
| sign test | 2 up / 3 down, p = 1.000 |
| worst "regression" | `mech_hard_pitcher_pump` **−0.206** |
| prompts to resolve ±0.02 | ~408 |

The shipping bar the wave was given — mean delta ≥ 0 and no prompt regressed by more than 0.03 —
is failed by the A/A itself, so at n = 8 it tests the instrument, not the change.  C0's worst
pair planned 1 part and 10 parts for the same pitcher pump: the dominant variance term is the
**planner**, and the deterministic readouts inherit it directly — on the same A/A,
`eval/bench/ab_gate_rates.py` saw penetrating pairs go **1 → 17** between identical arms
(mean +5.67 pairs, +2.80 mm worst depth): a bigger plan has more parts and more contacts.

**What the A/B measured** (`eval/bench/out/ab_skills`): one paired prompt scored out of eight in
a 503 storm, delta **+0.002** (0.937 → 0.939), `inconclusive` — and the variant arm had not read
the bundles (the 0 of 5 above).  Its first attempt was **void** with no symptom: `spawn_cell`
started each child as a file path, `ab_plan` imported `codeverse3d._compat` above its `sys.path`
bootstrap, and both arms ran the main tree with no `skills` package at all; fixed with a
bootstrap-first import, a guard that refuses a foreign `codeverse3d`, and
`eval/tests/test_worktree_import.py`.

**Next, in order:** pin the plan (removes the dominant term from both readouts and costs one
planner call less per pair); re-run on a post-D107 tree (the corpus no longer argues with
`c3d-part-contact` about weld overlap) with gate counts as the primary readout, as hit rates (§9).

---

## 7. Native loading — proven, not argued

`pytest -m live tests/skills/test_live_discovery.py`: a probe bundle is materialised into a
scratch workspace, the CLI gets a task matching its description, and the assertion is the read
probe plus a magic word that only lives in `references/`.

| CLI | first proven | re-run 2026-11-03 | discovery root | surfaced | deep |
|---|---|---|---|---|---|
| claude-code | 2.1.245 | 2.1.317 | `.claude/skills/` | yes | yes — via `cd …/<name> && … cat "$f"` (D115) |
| codex | 0.149.0 | 0.166.0 | `.agents/skills/` | yes | yes |
| agy | 1.1.20 | 1.4.12 | `.agents/skills/` | yes | yes |

Re-read from the binaries 2026-09-22 (claude-code 2.1.280, codex 0.155.1, agy 1.2.2) through the
harness's own launch path: same roots, all activate the routed bundles; agy's
`--disable-slash-commands`, which the harness passes, only stops `/name` expansion of the prompt.
Two things this settled that had only been argued: `Skill` was absent from `ALLOWED_TOOLS`
(claude-code would have denied its own skill tool — added); and the negative control — a bundle whose description does not match, "do not read any
files, do not activate any skill" — found every CLI opening `SKILL.md` **and** `references/`,
each only under the root it owns (claude-code both roots): the independent confirmation of the
root mapping, and the reason `deep` cannot mean "the agent chose to go deeper" on atime alone (§1).

---

## 8. How to add a skill

1. `codeverse3d/skills/<name>/SKILL.md` — `name` equals the directory name (lowercase `a-z0-9`,
   single hyphens); `description` (≤ 1024 chars) says WHAT and WHEN — the only text a CLI matches
   on; no `<` or `>` in the frontmatter (the spec's prompt-injection rule); spec keys only at the
   top level, everything of ours under `metadata`, where `evidence` and `verified` (a *quoted*
   ISO date) are required.
2. `codeverse3d/skills/<name>/references/*.md` — required, one level: without it the bundle can
   never score on the read metric.
3. `codeverse3d/skills/_claims/<name>.toml` — one row per number the body quotes from live code,
   so moving the constant breaks the test that ships the sentence:

   ```toml
   [[claim]]
   key    = "penetration_error_m"
   text   = "10 mm"                                              # must appear in the body
   python = "codeverse3d.spatial.connectivity:PENETRATION_ERROR_M"
   scale  = 1000
   format = "{:.0f} mm"
   ```
   A row may also pin a sentence in a bundle file (`file = "references/x.md"`), a JS constant
   (`js = "runtime_js/lib/backdrop.mjs:GROUND_SPAN_M"`), a sequence (`sep`, `last_sep`) and the
   line it must stand on (`context = "WARN below"`).  The prompt corpus pins its numbers the same
   way in `codeverse3d/prompts/_claims.toml` (`check_prompt_claims`, run by
   `3dcode skills validate`).
4. Its rows in `ROUTES` (`codeverse3d/skills/registry.py`), in the same commit.
5. A `Target` row in `codeverse3d/addons/skill_targets.py` and the matching `target_*` frontmatter
   keys — the ONE deterministic quantity the bundle claims to move, its direction, and its
   baseline from `python eval/bench/skill_targets.py eval/bench/out`; if no deterministic
   instrument can see the claim, say so with `measurable=False` and a `caveat`.
6. `3dcode skills validate --strict` and `pytest tests/skills`.

Body rules: ≤ 350 lines and ≤ 2,500 tokens; no code fence over 12 lines (D107); never restate a
frame, unit or naming rule (`conventions.py` owns those); every corpus percentage carries its
battery, its n and its date.

---

## 9. Known open

* **The mean-count readout is the wrong statistic.**  Target metrics are spiky (38–83 % of runs
  at 0); `P(run has ≥ 1 contract finding)` is 63 %, and halving *that* needs ~24 pairs where
  halving the mean needs ~449.  Converting the readout to a hit rate is the cheapest change that
  makes an effect A/B affordable.  Not done.
* **Pinning the plan does not collapse the gate-metric noise.**  Verified in production (one
  planner call per pair, both arms cache-HIT): `contract_findings` against `n_parts` gives
  R² = 0.103, and a pinned A/A pair scored 0.450 vs 0.654.  The spread is generation noise at a
  fixed plan size; do not budget an A/B on pinning buying power.
* **`codex` can drop a round from the denominator** — `skills.attached` with no `skills.read`
  and no `skills.jsonl` row, so an attached round can vanish from `3dcode skills report`.
* **Read rate confounds with routed-set size.**  1-skill sessions (glsl, opengl) read their
  bundle 4/4; 5-skill object sessions averaged ~55 %.  A wording A/B must hold the set size fixed
  or it measures the cap.
* **`c3d-part-contact`'s A/B predates D107.**  The corpus taught 2–5 mm of overlap, at or above
  `PENETRATION_WARN_M`, until D107 put every prompt on the gate's band (gap ≤ 2 mm, overlap
  ≤ 2 mm) — the single most likely reason the bundle failed to move its number.  Re-run on a
  post-D107 tree; never pool with before.
* **Runs before 2026-09-22 carry no `shader_preflight` gate** (it rides `BuildResult.gates`
  since then, so R21 fires): a readout across that date must not mix the two.
* **Corpus percentages are checked for provenance, not recomputed.**  A body's "47 graded
  blender runs" is a slice; enforced: a claim may not exceed what `eval/bench/out` holds,
  `measured` needs n ≥ 20, every rate names its battery / n / date.
* **`cadquery` and `threejs` bundles are unranked** until each language reaches 20 graded runs
  (4 and 3 at the last count; `C3D_SKILLS_UNVERIFIED=0` drops them).
* **Router precision is not reported** — how often a skill was read whose defect class never
  fired in that run; the number that decides whether the cap of 5 is too generous.
* **`scene_frames/content_*` has no classified kind**, so frame coverage — half of what
  `c3d-scene-composition` teaches — neither routes nor counts; adding a kind is an effect claim
  and needs its own evidence.
