# Skills

A **skill** is a task-scoped rule sheet the harness attaches to a coding session
automatically — from the track, the language, the round kind, the plan, and **the previous
round's gate findings**. Nobody writes `skills: [...]`.

It is not a renamed cookbook chapter. The cookbook (`codeverse3d/prompts/<lang>/cookbook.md`)
stays the reference manual with the copyable code; a skill states rules and numbers and
names the cookbook section to fetch. Two libraries of truth would be worse than one.

`SKILL.md` is an open standard (agentskills.io, Dec 2025), which is why the format below is
not ours to bend: **claude-code, codex, gemini-cli and agy discover these bundles
themselves** — proven live, see §7.  (The in-process `api-agent`, the one backend that
needed harness-side injection, was deleted 2026-08-28, and its routed index and
`read_skill` tool with it.)

Everything is behind `C3D_SKILLS`. **It ships OFF**, because `api-agent` read 0 of 5
routed bundles in the measured A/B while the three subscription CLIs read all five.
§6 has the numbers.

---

## 0. How the library is managed

**One library, one policy, thin adapters.**  The bundles under `codeverse3d/skills/<name>/`
are plain [agentskills.io](https://agentskills.io/specification) `SKILL.md` directories and
know nothing about any backend — anyone can `cp -r codeverse3d/skills/<name> ~/.claude/skills/`
and use them without this harness at all.  `router.py` picks the set from typed inputs.

Everything that genuinely differs between coding agents is **one bit**, and it lives in
one file, `skills/prompting.py`:

| backend | discovery root |
|---|---|
| `claude-code` | `.claude/skills` |
| `codex`, `gemini-cli`, `agy` | `.agents/skills` |

It was read out of the shipped binaries, not assumed.  All four have a native loader, so
none is handed a second index (it would list the same skills twice): the body file gets one
MANDATORY sentence.

**Adding a backend is one row in that table.**  A test (`tests/skills/test_delivery.py`)
fails if per-backend knowledge leaks back out into the other modules, and another checks
that `prompting.py` agrees with the policy rather than re-deriving it.


## 1. The model: progressive disclosure, and why it is also the measurement

The spec's three tiers, and what each costs us per turn:

| tier | what the agent sees | when | our cost |
|---|---|---|---|
| 1 · index | `name` + `description`, listed by the CLI's own loader | every turn | 41 tokens (our one mandate sentence) |
| 2 · body | the whole `SKILL.md` | on activation | 1,081–2,223 tokens, once |
| 3 · depth | `references/*.md` | when the agent chooses to go deeper | as read |

The same three tiers are the read probe. `materialize_skills` writes every file with
`atime == mtime`; the root filesystem is ext4 with `relatime`, so the first read afterwards
bumps `atime`:

* `atime(SKILL.md) > mtime` → **surfaced**: something opened the bundle.
* `atime(references/*.md) > mtime` → **deep**: something opened the body's depth file.

A bundle with no `references/` reports `deep_measurable: false` rather than quietly
scoring 0. `api-agent` owns its own `read_file`, so it logs exact reads with turn numbers.

### The probe carries its own falsification, because it had to

The design read `deep` as "the agent chose to go deeper". **Measured 2026-08-25, it does
not mean that.** Two independent causes, both reproduced:

1. **`Workspace.changed_files` reads every file.** It runs `git add -A -N` then
   `git diff --numstat` after *every* agent session to compute `files_changed`, and git
   reads each untracked file to diff it. On a real git workspace that alone flips all four
   files of every bundle to "read", with no agent involved. Reproduced in
   `test_git_diff_alone_trips_the_control`.
2. **Every CLI opens `references/` while activating a skill.** Negative control: one
   bundle whose description does not match the task, prompt "do not read any files, do not
   activate any skill" — codex 0.149.0, claude-code 2.1.245, agy 1.1.20 and gemini-cli
   0.53.0 *all* opened `SKILL.md` **and** `references/`. (Each touched only the root it
   owns, which is a nice independent confirmation of the root mapping in §7.)

So a fourth signal was added: `materialize.write_control` puts one **never-routed,
never-indexed** bundle beside the real ones. Nothing should ever open it. When it comes
back opened, `SkillsUsage.control_read` is set, `probe_trustworthy` is false and
the session's deep-read rate is **`None`** — not 100%, not 0. `3dcode skills report` excludes those
sessions from the rate and prints how many it dropped.

This is the difference between a metric and a number that would have read 100% forever.

---

## 2. The library

**Thirteen bundles.** `lines`/`tokens` are the body, against caps of 350 and 2,500.

| skill | track | language | evidence | lines | tokens | routes |
|---|---|---|---|---|---|---|
| `c3d-part-contact` | object tracks | any | measured | 125 | 1,707 | R1, R2 |
| `c3d-bbox-contract` | object + scene | any | measured | 75 | 1,081 | R3, R4 |
| `c3d-repeats-and-mirrors` | object tracks | any | measured | 112 | 1,527 | R6, R7 |
| `c3d-blender-forms` | any | blender, urdf_blender | measured | 97 | 1,811 | R8, R9 |
| `c3d-urdf-joints` | articulated_object | urdf_blender | measured | 139 | 1,989 | R12, R13 |
| `c3d-scene-composition` | scene | scene_threejs | mixed | 118 | 1,691 | R14, R15 |
| `c3d-scene-lighting` | scene | scene_threejs | mixed | 126 | 1,959 | R16, R17 |
| `c3d-scene-motion` | scene | scene_threejs | mixed | 178 | 2,223 | R18, R19 |
| `c3d-threejs-shader-traps` | scene + static | threejs both | mixed | 112 | 1,734 | R20, R21 |
| `c3d-glsl-craft` | graphics | glsl_shader | mixed | 140 | 1,958 | R22, R24-glsl |
| `c3d-opengl-pipeline` | graphics | opengl_python | mixed | 98 | 1,497 | R23, R24-opengl |
| `c3d-cadquery-forms` | any | cadquery | **inherited-unverified** | 82 | 1,482 | R10 |
| `c3d-threejs-forms` | static_object | threejs | **inherited-unverified** | 120 | 1,929 | R11 |

**All seventeen ship OFF** (`C3D_SKILLS` unset). Zero have a measured effect; see
`docs/SKILLS_LEDGER.md` §5 for why that is the honest default and what flips one on.
Shipping a subset needs no new machinery — `C3D_SKILLS_ONLY` restricts the library before
routing, so `C3D_SKILLS=1 C3D_SKILLS_ONLY=<names>` is the vehicle when a bundle earns it.

### Retired

| skill | cut | why |
|---|---|---|
| `c3d-form-manifest` | 2026-08-25 | opened **2 of 19** times (11%, CI [3%, 31%]) — unread before *and* after its description was rewritten to lead with the trigger. The only bundle whose read-rate interval excluded every other's, and it cost ~1,917 tokens in 19 of the 27 sessions that could have read it. Text and full numbers: `docs/skills-attic/c3d-form-manifest/` |

An attic bundle is not loaded: `all_skills()` reads `codeverse3d/skills/*/SKILL.md` only, so it
costs no index line and no tokens. Its route id (**R5**) is retired and never reused — a run
record refers to a routing decision by id, so recycling it would silently relabel history.
`tests/skills/test_registry.py::RETIRED_RULES` pins that.

### Two words this doc keeps apart

`evidence` is about **provenance** — where the bundle's prose came from. A bundle can be
`measured` for provenance and have **no measured effect**; five are. Every bundle also
declares **one deterministic quantity it claims to move**, in its own frontmatter
(`target_metric`, `target_direction`, `target_baseline`) and in `codeverse3d/addons/skill_targets.py`.
`python eval/bench/skill_targets.py eval/bench/out` prints them all — per battery or paired across an
A/B's two arms — and **`docs/SKILLS_LEDGER.md` is the row-by-row maintenance surface**.

`cadquery-forms` and `threejs-forms` stay `inherited-unverified` and routed off: their
languages have **4 (cadquery) and 3 (threejs) graded runs** against the 20 the rule
requires (design §5.2 law 3).

## 3. Routing

`codeverse3d/skills/registry.py` holds the typed table (R1–R24) and `finding_kind()`, the one
place a gate message is pattern-matched; its router half (`select()`, `skills_for()`) turns
`(track, language, kind, plan signals, findings)` into a ranked, capped, reasoned set.

Four laws, all tested:

1. **Gate findings outrank everything.** A row that answers a finding sits at priority
   ≥ 90; standing rows sit at ≤ 80. A repair round spends its budget on what broke. This is
   the input no CLI's own skill loader can see, and the reason we route in code.
2. **The cap is 5** (`C3D_SKILLS_MAX`). It cuts the low-priority tail, never the
   gate-fired head.
3. **Quiet kinds attach nothing** on their own — `asset`, `asset_fix`, `reference` are
   short, narrow sessions with no defect class attached to them. A gate-fired row still
   reaches them (R13/R15/R17/R19/R21 are kind-agnostic; R2/R4/R7/R9 are not — both halves
   are pinned in `test_router`).
4. **Unknown input degrades to `[]`, never an exception.** A planner failure must not also
   take out the round's skills.

`test_routing_property.py` runs all of that over the real library and the whole input
space: every track × language × round kind × 256 plan-signal combinations × both
`allow_unverified` settings (43,008 sessions per track), every gate finding kind the
corpus produces, and 2,000 seeded random walks including junk tracks and languages.

`finding_kind()` classifies **wider** than it routes — 113 distinct WARN/ERROR message
shapes mined from `eval/bench/out`, with a 43-row golden fixture
(`tests/skills/data/gate_findings.json`) asserting no `unknown`. Naming a defect costs
nothing; routing one is a claim that a skill helps. INFO findings are census, not defects,
and classify to `None` — routing off "all 7 parts are connected" would attach the
interpenetration sheet to a run with no interpenetration.

Adding a bundle means adding its rows in the same commit: an unroutable bundle pays the
index and never helps, and a test fails on both halves of that.

---

## 4. Telemetry

Per round, `RoundRecord.skills` carries a `SkillsUsage`: what was listed, what was
surfaced, what was read deep, `index_tokens`, `body_tokens_read`. Each attached body's hash
goes into `record.prompt_hashes` as `skill:<name>`, so "what text decided this run" stays
answerable. Per session, `telemetry/skills.jsonl` gets one line.

```
3dcode skills report eval/bench/out/<battery>          # deep-read rate per skill per backend
3dcode skills report eval/bench/out/<battery> --json
3dcode doctor --skills                            # library + discovery wiring
```

Targets: ≥ 60% deep-read for CLI backends (the api-agent's 80% target died with it, 2026-08-28), counted only over sessions
the control (§1) says were measurable. **A skill under 20% over 20 such sessions is merged
or deleted.** A library that only ever grows is how this ends as bloat.
The read rate is the FIRST readout, before any score: `read_cookbook` was called by 0 of 16
zone sessions on `scenes_v1_flash` although the prompt named five chapters by title, and
shipping a nicer file format without measuring reads would repeat that at a new price.

### Measured, on an ordinary build task

Not a compliance prompt — "write `src/model.py` and `src/parts/*.py` for a four-legged
dining chair, follow whatever conventions this workspace documents", five routed bundles
sitting in the workspace, nothing in the prompt about skills (2026-08-25):

| backend | listed | surfaced | deep | deep rate | body tokens read | exit |
|---|---|---|---|---|---|---|
| codex 0.149.0 | 5 | 5 | 5 | **100%** | 8,041 | completed, 18 turns |
| claude-code 2.1.245 | 5 | 5 | 5 | **100%** | 8,041 | timed out on our 600 s cap after reading |
| agy 1.1.20 | 5 | 5 | 5 | **100%** | 8,041 | completed |

**Read those 100%s as an upper bound.** These runs predate the control bundle, and §1 says
why every one of them would report 100% whether or not the agent chose to read anything.

What is *not* ambiguous is the behavioural evidence underneath: codex's
`src/parts/common.py` came back with `WELD_OVERLAP = 0.001` and the comment
"1 mm weld overlap with the seat" — `c3d-part-contact`'s recommendation, inside
`PENETRATION_WARN_M`. Left to the prompt corpus alone it would have read 2–5 mm (§9). The
content reached the model and changed the output; the atime number is what cannot prove it.

Two things this also says, and neither is comfortable:

* **They read ALL five.** 8,041 tokens per session, every time. The cap of 5 is therefore
  the real cost knob, not a safety net, and router *precision* — how often a skill is read
  whose defect class never fires — is the number that should size it (§9).
* **`api-agent` is not in this table.** It needs the Gemini pool, which was in an outage
  while this was measured, so the backend with the strictest target (80%) and the only
  exact-read ground truth is the one still unmeasured.

---

## 5. What the tests guarantee

`tests/skills/` keeps the default checks hermetic.  The two `slow`-marked cases
build a wheel (still selected by default; use `-m "not slow"` to omit them), and
the `live` cases drive a real CLI.

| file | guarantees |
|---|---|
| `test_loader.py` | the spec's field rules, as the loader enforces them |
| `test_spec_compliance.py` | the same rules re-derived from the raw bytes, **plus the reference validator** (`pip install skills-ref` → the `agentskills` CLI) and a field-by-field agreement check between its `read-properties` and our loader |
| `test_library.py` | body budget, no 20-line code fences, no restating `conventions.py`, every `_claims` number still matches its live constant, **no two skills point one claim key at different numbers** |
| `test_freshness.py` | every tool, gate kind, rubric criterion, constant, switch, sibling skill and cookbook section a bundle names still exists |
| `test_router.py` / `test_routing_property.py` | the four routing laws, by row and over the whole input space |
| `test_telemetry.py` | the read probe, **including the control that catches git reading the tree** |
| `test_packaging.py` | **a built wheel contains all 13 `SKILL.md`, all 13 `references/`, all 9 `_claims`** |
| `test_live_discovery.py` | §7 — a real CLI actually finds and opens a bundle |

Two contradiction checks are worth separating, because they answer different questions:

* **value-level**, library-wide: two skills may not point one claim key at different
  numbers. Compared on the *pre-scale value*, not the rendered text — `c3d-bbox-contract`
  says "1 cm" and `c3d-repeats-and-mirrors` says "0.01" metres, and both are right.
  Failing that pair would teach the next author to delete the claim.
* **text-level**, co-routing only: two skills that can land in one session and chose the
  same units must read the same.

### What these tests caught

* **Three `contract.md` files told the agent to interpenetrate.** `blender` said "2–4 mm
  overlap is fine", `cadquery` "overlap (≥ 2 mm) … is required", and `threejs`'s worked
  example drove a leg 4 mm into a seat — while `connectivity.py` WARNs above
  `PENETRATION_WARN_M = 2 mm` and its own `fix_hint` says "overlap by ≤ 2 mm". Fixed, and
  the check is now standing.
* **Four bundles' `verified:` dates were YAML dates, not strings** — the spec says metadata
  is string→string. Quoted; `validate_bundle` now reports it instead of coercing.
* **`c3d-opengl-pipeline` claimed `measured` on n=5.** Now `mixed`.
* **The api-agent index cost 780 tokens for five skills** against a 300-token budget,
  because it quoted whole 1024-char descriptions. It quotes the first clause now (356
  tokens for the worst real session, 400 is the ceiling): our router already decided, so
  that index is a pointer, not a matcher.
* **The live smoke had never run.** It skipped every CLI because it looked for a binary
  named after the agent kind — `gemini-cli` runs `gemini`, `claude-code` runs `claude`.
* **A body could name one of our constants without pinning it.** `c3d-glsl-craft` quoted
  `DUPLICATE_DIFF` at `1e-4` with no claim row behind it, so moving the constant would have
  left a confident sentence with the old number and nothing to notice.
* **The read probe was measuring git**, not the agent (§1). The most consequential finding
  of the pass, and the reason the control bundle exists.

---

## 6. Does it help? The A/B

**Verdict: ship OFF behind `C3D_SKILLS`.**

### The rig

`eval/bench/ab_plan.py`, arms differing in exactly one thing — `C3D_SKILLS=on` on the variant —
on `static_objects_v2`, `--rounds 1`, paired, generator `gemini-cli:gemini-3.6-flash`,
fixed judge `gemini:gemini-3.1-pro-preview` at `n_samples 2`, `--max-in-flight 8` against
32–48 of pool headroom, `--wait-for-provider`. The eight prompt ids are **the same eight
`eval/bench/out/plan_loop/C0` used for its A/A**, so the noise floor below was measured on this
exact battery, on this rig, on this day.

The switch is generation-side: the planner call happens in the `plan` stage before any
skill is attached, and `attach_for_round` only touches generation and repair rounds. That
matters for §8.1 of `eval/docs/EVAL.md` and is the reason the next run of this can pin the plan.

### The noise floor, measured first

`eval/bench/out/plan_loop/C0`, 2026-08-25, **two identical arms** on these eight prompts:

| | |
|---|---|
| paired prompts scored | 6 of 8 (2 lost to `infra_failed`) |
| mean delta | **+0.043** — from nothing |
| paired sd | 0.202 · SE 0.082 · 2 SE band **±0.165** |
| separated from noise | **NO** |
| sign test | 2 up / 3 down, p = 1.000 |
| worst "regression" | `mech_hard_pitcher_pump` **−0.206** |
| prompts needed to resolve ±0.02 | ~408 |

Read the last two rows against the shipping bar this wave was given — *mean delta ≥ 0 and
no prompt regressed by more than 0.03*. **The A/A fails that bar.** Two of its six paired
prompts regressed past 0.03 with byte-identical arms. So at n = 8 on this rig the bar is
not a test of the change; nothing can pass it. That is a fact about the instrument, and it
is the honest first line of any verdict it produces.

`eval/docs/EVAL.md` §8.1 opened C0's worst pair and found the cause: the two identical arms
planned 1 part and 10 parts for the same pitcher pump. The dominant variance term is the
**planner**, not the judge and not the generator.

### The first attempt measured nothing, and said nothing about it

Recorded here because it is the more useful half of this section. The first run of this
A/B was **void**: `ab_plan.spawn_cell` starts each child as a file path, so `sys.path[0]`
is `eval/bench/`, and `ab_plan` imported `codeverse3d._compat` *above* its own `sys.path`
bootstrap — which let the editable install resolve `codeverse3d` to the **main tree**. Both
arms ran a harness with no `codeverse3d/skills` package at all. No error, no warning; the
variant workspace simply had no `.agents/skills` directory and no `skills.attached` event,
and the run would have reported "no effect" with a straight face.

The plan-loop wave hit the same thing hours earlier
(`eval/bench/out/plan_loop/C0/invalid_attempt1_maintree_import`) and worked around it with
`PYTHONPATH` in a launch script. It is fixed in the code now — bootstrap first, a guard
that refuses to start against a foreign `codeverse3d`, and
`tests/compare_bench/test_worktree_import.py` — because a workaround protects whoever
remembers it, not the run.

### What this A/B actually measured

`eval/bench/out/ab_skills`, **still running when this was written** — re-read `summary.md` for the
current state, and re-run with `--redo-status infra_failed` before quoting it as final.
Weather was hostile: the Gemini pool sat at 0–4 of 6 keys answering for most of the window,
so the rig parked on its preflight for two hours and then lost whole pairs to 503 storms
mid-cell.

| | |
|---|---|
| paired prompts scored | **1** of 8 (`ctrl_med_dining_chair`) |
| mean delta | **+0.002** (control 0.937, variant 0.939) |
| 95% CI | **not computable at n = 1**; the A/A band on this battery is ±0.165 |
| sign test | 1 up / 0 down, p = 1.000 |
| `infra_failed` cells | **3+** (both arms of `ctrl_med_toaster`, control of `mech_hard_coffee_grinder`), excluded, never scored 0 |
| verdict printed by the rig | `inconclusive`, `separated from noise: NO` |

**And the reason that delta is +0.002 is not that the skills did not help. It is that the
variant arm never read them.**

### The read rate, with ground truth: 0 of 5

`api-agent` is the calibration arm — we own its `read_file` tool, so
`telemetry/skill_reads.jsonl` is not a probe, it is a log. On the scored variant cell:

| | |
|---|---|
| skills listed in message 0, marked MANDATORY | **5** |
| index cost | 356 tokens |
| `read_file` calls the agent made, across 4 sessions | **52** |
| of those, on a skill path | **0** |
| `read_cookbook` calls instead | 2 |
| what the atime probe reported | 5 of 5 "deep" |
| what the control reported | `control_read: true` — **the probe was blind**, exactly as §1 predicts |

The next variant cell (`mech_hard_coffee_grinder`) repeated it: five bundles materialised,
`read_file` called, **zero** skill paths.

So on the backend the entire bench runs on, the mechanism is inert: the agent read 52 files
and not one of them was a skill. The two arms differed by 356 tokens of index that nobody
opened, and +0.002 is what that is worth.

This is not the same answer for every backend. The three subscription CLIs read all five
unprompted (§4) and codex's output carried the skill's number. The difference is the
affordance: a CLI has a first-class skill tool its runtime surfaces, and `api-agent` has a
line in a long markdown file plus a generic `read_file`.

### Verdict

**Ship OFF behind `C3D_SKILLS`**, and not because the number was negative:

1. On `api-agent`, the read rate is **0 %** with ground truth. Shipping a default-on feature
   that its main generator does not use would pay 356 tokens a turn for nothing.
2. The shipping bar it was given — mean delta ≥ 0 and no prompt regressed by more than 0.03
   — **cannot be cleared by anything on this rig at n = 8**: the A/A of two identical arms
   fails it, twice over (−0.038, −0.206).
3. The deterministic readouts are not an escape hatch either (below).
4. Two of the original thirteen bundles have no graded runs behind them at all and are already routed off; the four 2026-09-01 scene-graphics ports are `inherited-unverified` by construction until the sceneloop A/B grades them.

Nothing here says the library is wrong. The CLI evidence says the opposite. It says the
delivery mechanism for `api-agent` is a pointer nobody follows, and that is fixable.


### The deterministic readouts are not the escape hatch either

The design planned around this: make the **gate counts** the primary readout, since they
are deterministic and are what a contact skill actually targets, and put the judged score
second under a sign test. `eval/bench/ab_gate_rates.py` computes them, paired per prompt.

Run it over the SAME A/A — two identical arms — and it says:

| readout (variant − control, identical arms) | mean delta | better / worse / tied |
|---|--:|---|
| penetrating pairs | **+5.67** | 0 / 2 / 1 |
| worst penetration depth (mm) | **+2.80** | 0 / 3 / 0 |
| floating parts | +0.00 | 0 / 0 / 3 |
| stray islands | −0.33 | 1 / 0 / 2 |
| contract findings | −1.00 | 2 / 1 / 0 |

`mech_hard_pitcher_pump` alone went **1 penetrating pair → 17** between two identical arms.
Of course it did: the same §8.1 planner variance that produced a 1-part plan and a 10-part
plan produces 1 contact pair and 17. The gate counts inherit the planner's spread *directly*
— a bigger plan has more parts, more parts have more contacts. **They are not the low-noise
readout the design hoped for**, and this A/A calibration of them did not exist before today.

So at n = 8 with a free plan, neither readout can separate this switch from nothing.

### What to try next, in order

0. **Pin the plan** (`eval/docs/EVAL.md` §8.1). The skills switch is generation-side — the
   planner runs in the `plan` stage before anything is attached — so plan-once-write-both is
   valid here, and it removes the dominant variance term from *both* readouts instead of
   averaging it down. It also costs one planner call *less* per pair. This is the single
   highest-value change and nothing else is worth running before it.
1. **Reconcile the prompt corpus on weld overlap** (§9). `contract.md` is fixed, but
   `tracks/generate_static.j2` still says "overlap neighbours by ≥ 0.002 m (push a leg
   2-5 mm into the seat)". While that stands, `c3d-part-contact` is arguing with the
   prompt inside the same session, and the pair-count readout is measuring the argument.
2. Only then re-run this A/B, with the gate counts as the primary readout.

---

## 7. Native loading — proven, not argued

`pytest -m live tests/skills/test_live_discovery.py`. A probe bundle is materialised into a
scratch workspace, the CLI is given a task matching its description, and the assertion is
the read probe itself plus a magic word that only lives in `references/`.

| CLI | version | discovery root | surfaced | deep | followed |
|---|---|---|---|---|---|
| gemini-cli | 0.53.0 | `.agents/skills/` | yes | yes | yes |
| codex | 0.149.0 | `.agents/skills/` | yes | yes | yes |
| claude-code | 2.1.245 | `.claude/skills/` | yes | yes | yes |
| agy (antigravity) | 1.1.20 | `.agents/skills/` | yes | yes | yes |

All four discover the library natively, with no injection from us. Two things this settled
that had only been argued statically:

* `Skill` was **absent** from `ALLOWED_TOOLS` in `agents/claude_code.py`, so claude-code
  would have denied its own skill tool. Added; `3dcode doctor --skills` now checks it.
* gemini-cli's `activate_skill` consent prompt does not block us under
  `--approval-mode yolo`. This was the one claim that could not be verified statically.

**And the same run refutes the surfaced-vs-deep split.** Negative control, same rig: a
bundle whose description does *not* match the task, prompt "do not read any files, do not
activate any skill". All four CLIs opened `SKILL.md` **and** `references/`:

| CLI | `.agents/` SKILL.md | `.agents/` references | `.claude/` SKILL.md | `.claude/` references |
|---|---|---|---|---|
| codex | opened | **opened** | — | — |
| agy | opened | **opened** | — | — |
| gemini-cli | opened | **opened** | — | — |
| claude-code | opened | **opened** | opened | **opened** |

Each touched only the root it owns (claude-code reads both), which is a second, independent
confirmation of the mapping above — and the reason `deep` had to stop meaning "the agent
chose to go deeper" (§1).

---

## 8. How to add a skill

1. `codeverse3d/skills/<name>/SKILL.md` — `name` equals the directory name, lowercase
   `a-z0-9` and single hyphens; `description` (≤ 1024 chars) says WHAT and WHEN, because it
   is the only text a CLI matches on; **no `<` or `>` anywhere in the frontmatter** (the
   spec's prompt-injection rule); only spec keys at the top level, everything of ours under
   `metadata`; `metadata.evidence` and `metadata.verified` (a *quoted* ISO date) required.
2. `codeverse3d/skills/<name>/references/*.md` — **required**, one level. Without it the
   bundle can never score on the read metric.
3. `codeverse3d/skills/_claims/<name>.toml` — one row per number the body quotes from live
   code, so moving the constant breaks the test that ships the sentence:

   ```toml
   [[claim]]
   key    = "penetration_error_m"
   text   = "10 mm"                                              # must appear in the body
   python = "codeverse3d.spatial.connectivity:PENETRATION_ERROR_M"
   scale  = 1000
   format = "{:.0f} mm"
   ```
4. Add its rows to `ROUTES` in `codeverse3d/skills/registry.py`, in the same commit.
5. Add a `Target` row to `codeverse3d/addons/skill_targets.py` and the matching `target_*` keys to
   the frontmatter — the ONE deterministic quantity the bundle claims to move, its
   direction, and its baseline from `python eval/bench/skill_targets.py eval/bench/out`. If no
   deterministic instrument can see the claim, say so with `measurable=False` and a
   `caveat`: that is a finding about the bundle, not a gap to paper over with a judged
   criterion. Then add its row to `docs/SKILLS_LEDGER.md`.
6. `3dcode skills validate --strict` and `pytest tests/skills`.

Body rules: ≤ 350 lines and ≤ 2,500 tokens; no code fence over 20 lines; never restate a
frame, unit or naming rule (`conventions.py` owns those); every corpus percentage carries
its battery, its n and its date.

---

## 9. Known open

Updated 2026-08-25 (curate wave). Items the three measurement waves **closed** are marked
so; the rest are live.

### Closed this wave

* ~~**The scene track never delivered its skills.**~~ **FIXED.** `SceneTrack.prepare()`
  generated the whole baseline (`_env_stage`, `_zones_stage`, `_assemble_stage`) without ever
  reaching `skills_hook.attach_for_round`, which lived only in `steps.run_round`; and
  `baseline_tasks` returned `[]`, so round 0 listed four bundles to a session that did not
  exist. Measured at **0 opens / 30 listings**. The generating stages now attach and record with
  their own kind (`scene.js` is assembled without a session since the compose fallback went,
  2026-09-22). `tests/skills/test_delivery_reaches_the_session.py` runs a real
  two-zone-session scene and proves env/zone attach exactly once, before generation,
  with matching telemetry. **The scene bundles' read rate against working delivery
  is UNMEASURED — that is the next wave's first experiment.**
* ~~**`api-agent` has no skill affordance.**~~ **FIXED earlier**, and re-measured here: with
  `read_skill` it opens **58%** of listed bundles (51/88) across sessions that are offered
  the tool, against 0/5 before the tool existed.
* ~~**The A/B rig scored four of seven languages 0.0.**~~ **FIXED.**
  `compare_backends._run_harness` gated on `src/model.py`, so a glsl/threejs/scene/opengl run
  that finished successfully was recorded `no_code`. Both arms scored 0.0 and the rig would
  have reported "no effect" for a switch it never tested.

### Live

* **The mean-count readout is the wrong statistic, and it is what everything was measured
  on.** Target metrics are spiky (38–83% of runs at 0). `P(run has ≥1 contract finding)` is
  63% and halving *that* needs ~24 pairs; halving the *mean* needs ~449. **Converting the
  readout to a hit rate is the single highest-value cheap change available** and it is what
  makes any effect A/B affordable. Not done.
* **Pinning the plan does not collapse the gate-metric noise.** Verified in production (one
  planner call per pair, both arms cache-HIT), and it is still cheaper than not pinning — but
  `contract_findings` regressed on `n_parts` gives R² = 0.103, and a pinned A/A pair scored
  0.450 vs 0.654 on identical arms. The spread is *generation* noise at fixed plan size.
  Do not budget an A/B on the assumption that pinning buys power.
* **`shader_preflight` is not a gate.** `check_shaders` is an agent-invocable tool, never a
  pipeline step, so it reaches no `record.json`. `c3d-threejs-shader-traps` is therefore
  **not instrumented**, which is a stronger statement than "always clean". Merge it into
  `rounds[].gates` before spending anything on that bundle.
* **`eval/bench/_fixed_eval.FixedEvaluator` is blender-only.** It pins `get_runtime(BLENDER)` and
  runs only lint and connectivity — never contract, joint_sweep, scene_frames or gl_frames.
  The *judged* column of any non-blender A/B is meaningless. The primary readout is safe:
  `eval/bench/skill_targets.py` reads each arm's own harness record.
* **The atime probe is blind in any git workspace, and its control proves it.** The control
  bundle came back "opened" in 27 of 33 sessions; the only 6 sessions where it stayed clean
  were the 6 scene sessions, where nothing was delivered. So the shipped report is honest
  only where there was nothing to see. Use trajectory `read_skill` calls as ground truth.
* **CLI backends' read rate is UNMEASURED — not 5/5.** `claude_code.py` uses
  `--output-format json`, which returns only the final result and no tool stream; the logs
  contain zero occurrences of any `c3d-` name. Switching to `--output-format stream-json`
  would give the CLI arm the same exact ground truth api-agent has. The earlier "5 of 5"
  claim rested on the atime probe and should not be repeated.
* **`codex` can drop a round from the denominator.** It emitted `skills.attached` with no
  `skills.read` and no `skills.jsonl` row, so an attached round can silently vanish from
  `3dcode skills report`.
* **Read rate confounds with routed-set size.** Every 1-skill session (glsl, opengl) read its
  bundle 4/4 while 5-skill object sessions averaged ~55%. Any wording A/B must hold the
  routed set size fixed or it measures the cap, not the copy.
* **The wider prompt corpus still contradicts the connectivity gate on weld overlap.**
  `contract.md` was fixed for all three languages, but `tracks/generate_static.j2`
  ("overlap neighbours by ≥ 0.002 m"), `tracks/assemble_static.j2`,
  `generate_static_part.j2`, `system/harness_contract.md` ("seams overlap by ≥ 2 mm") and
  three cookbooks still teach 2–5 mm, at or above `PENETRATION_WARN_M`. **This is the single
  most likely reason `c3d-part-contact` would fail to move the number it targets, and it is
  a prompt-corpus change with its own measurement. Do it before re-running that A/B.**
* **`ENV_RECIPES` / `ZONE_RECIPES` inlining in `tracks/scene.py` is untouched** — up to 6,000
  unconditional tokens per env/zone session, never A/B'd against a routed skill. Now that the
  scene stages deliver skills, this is the obvious comparison.
* **Corpus percentages are checked for provenance, not recomputed.** A body's "47 graded
  blender runs" is a *slice*; without each claim declaring its query, a tight recomputation
  compares two different populations. Enforced today: a claim may not exceed what `eval/bench/out`
  holds, `measured` needs n ≥ 20, every rate names its battery / n / date.
* **`cadquery` and `threejs` bundles are routed off** until each language reaches 20 graded
  runs. As of this wave: cadquery **4**, threejs **3**.
* **Router PRECISION is not reported.** The read rate says how often a listed skill was read;
  nothing says how often a skill was read whose defect class never fired in that run. That
  number decides whether the cap of 5 is too generous.
* **`scene_frames/content_*` has no classified kind**, so frame coverage — half of what
  `c3d-scene-composition` teaches — neither routes nor counts. Adding a kind widens routing,
  which is an effect claim and needs its own evidence.
