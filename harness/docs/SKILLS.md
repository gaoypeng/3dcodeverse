# Skills

A **skill** is a task-scoped rule sheet the harness attaches to a coding session
automatically — from the track, the language, the round kind, the plan, and **the previous
round's gate findings**. Nobody writes `skills: [...]`.

It is not a renamed cookbook chapter. The cookbook (`codeverse/prompts/<lang>/cookbook.md`)
stays the reference manual with the copyable code; a skill states rules and numbers and
names the cookbook section to fetch. Two libraries of truth would be worse than one.

`SKILL.md` is an open standard (agentskills.io, Dec 2025), which is why the format below is
not ours to bend: **claude-code, codex, gemini-cli and agy discover these bundles
themselves** — proven live, see §7. Only `api-agent` needs harness-side injection.

Everything is behind `CV3D_SKILLS`. **It ships OFF.** §6 says why, with the numbers.

---

## 1. The model: progressive disclosure, and why it is also the measurement

The spec's three tiers, and what each costs us per turn:

| tier | what the agent sees | when | our cost |
|---|---|---|---|
| 1 · index | `name` + one clause of `description` | every turn | 41 tokens (native CLIs) / ≤ 400 (api-agent) |
| 2 · body | the whole `SKILL.md` | on activation | 1,081–2,223 tokens, once |
| 3 · depth | `references/*.md` | when the agent chooses to go deeper | as read |

The same three tiers are the read probe. `materialize_skills` writes every file with
`atime == mtime`; the root filesystem is ext4 with `relatime`, so the first read afterwards
bumps `atime`. A CLI's discovery scan opens `SKILL.md` to read the frontmatter — verified,
§7 — and nothing scans `references/`. So:

* `atime(SKILL.md) > mtime` → **surfaced**: the bundle reached the agent's index.
* `atime(references/*.md) > mtime` → **deep**: the agent read the body and followed it.

A bundle with no `references/` reports `deep_measurable: false` rather than quietly
scoring 0. `api-agent` owns its own `read_file`, so it logs exact reads with turn numbers
and is the **calibration arm** for the probe.

`references/` is therefore not optional decoration. It is the only honest evidence that a
skill was read rather than merely listed.

---

## 2. The library

Fourteen bundles. `lines`/`tokens` are the body, against caps of 350 and 2,500.

| skill | track | language | evidence | lines | tokens | routes |
|---|---|---|---|---|---|---|
| `cv3d-part-contact` | object tracks | any | measured | 125 | 1,707 | R1, R2 |
| `cv3d-bbox-contract` | object + scene | any | measured | 75 | 1,081 | R3, R4 |
| `cv3d-form-manifest` | object tracks | any | measured | 155 | 1,917 | R5 |
| `cv3d-repeats-and-mirrors` | object tracks | any | measured | 112 | 1,527 | R6, R7 |
| `cv3d-blender-forms` | any | blender, urdf_blender | measured | 97 | 1,809 | R8, R9 |
| `cv3d-urdf-joints` | articulated_object | urdf_blender | measured | 139 | 1,989 | R12, R13 |
| `cv3d-scene-composition` | scene | scene_threejs | mixed | 118 | 1,691 | R14, R15 |
| `cv3d-scene-lighting` | scene | scene_threejs | mixed | 86 | 1,210 | R16, R17 |
| `cv3d-scene-motion` | scene | scene_threejs | mixed | 178 | 2,223 | R18, R19 |
| `cv3d-threejs-shader-traps` | scene + static | threejs both | mixed | 112 | 1,734 | R20, R21 |
| `cv3d-glsl-craft` | graphics | glsl_shader | mixed | 140 | 1,958 | R22, R24-glsl |
| `cv3d-opengl-pipeline` | graphics | opengl_python | mixed | 98 | 1,497 | R23, R24-opengl |
| `cv3d-cadquery-forms` | any | cadquery | **inherited-unverified** | 82 | 1,470 | R10 |
| `cv3d-threejs-forms` | static_object | threejs | **inherited-unverified** | 120 | 1,934 | R11 |

`evidence` is a claim about **n**, and `tests/skills/test_corpus.py` recomputes it from
`bench/out`:

* `measured` — the routed language has ≥ 20 graded runs behind it (blender 139,
  urdf_blender 23 as of 2026-08-25).
* `mixed` — real data, thin (glsl_shader 9, opengl_python 5, scene_threejs 3). Owes an
  `evidence_note` saying so.
* `inherited-unverified` — **zero** graded runs (cadquery, threejs). **Not routed** unless
  `CV3D_SKILLS_UNVERIFIED=on`. The rule that produced this label caught a real over-claim:
  `cv3d-opengl-pipeline` shipped as `measured` on n=5 and is now `mixed`.

---

## 3. Routing

`codeverse/skills/registry.py` holds the typed table (R1–R24) and `finding_kind()`, the one
place a gate message is pattern-matched. `codeverse/skills/router.py` turns
`(track, language, kind, plan signals, findings)` into a ranked, capped, reasoned set.

Four laws, all tested:

1. **Gate findings outrank everything.** A row that answers a finding sits at priority
   ≥ 90; standing rows sit at ≤ 80. A repair round spends its budget on what broke. This is
   the input no CLI's own skill loader can see, and the reason we route in code.
2. **The cap is 5** (`CV3D_SKILLS_MAX`). It cuts the low-priority tail, never the
   gate-fired head.
3. **Quiet kinds attach nothing** on their own — `asset`, `asset_fix`, `reference` are
   short, narrow sessions with no defect class attached to them. A gate-fired row still
   reaches them (R13/R15/R17/R19/R21 are kind-agnostic; R2/R4/R7/R9 are not — both halves
   are pinned in `test_router`).
4. **Unknown input degrades to `[]`, never an exception.** A planner failure must not also
   take out the round's skills.

`test_routing_property.py` runs all of that over the real library and the whole input
space: every track × language × round kind × 64 plan-signal combinations (5,376 sessions
per track), every gate finding kind the corpus produces, and 2,000 seeded random walks
including junk tracks and languages.

`finding_kind()` classifies **wider** than it routes — 113 distinct WARN/ERROR message
shapes mined from `bench/out`, with a 43-row golden fixture
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
3dcv skills report bench/out/<battery>          # deep-read rate per skill per backend
3dcv skills report bench/out/<battery> --json
3dcv doctor --skills                            # library + discovery wiring
```

Targets: ≥ 60% deep-read for CLI backends, ≥ 80% for api-agent. **A skill under 20% over 20
sessions is merged or deleted.** A library that only ever grows is how this ends as bloat.
The read rate is the FIRST readout, before any score: `read_cookbook` was called by 0 of 16
zone sessions on `scenes_v1_flash` although the prompt named five chapters by title, and
shipping a nicer file format without measuring reads would repeat that at a new price.

---

## 5. What the tests guarantee

`tests/skills/` — 590 offline tests, plus 4 live and 3 slow.

| file | guarantees |
|---|---|
| `test_loader.py` | the spec's field rules, as the loader enforces them |
| `test_spec_compliance.py` | the same rules re-derived from the raw bytes, **plus the reference validator** (`pip install skills-ref` → the `agentskills` CLI) and a field-by-field agreement check between its `read-properties` and our loader |
| `test_library.py` | body budget, no 20-line code fences, no restating `conventions.py`, every `_claims` number still matches its live constant, **no two skills point one claim key at different numbers** |
| `test_freshness.py` | every tool, gate kind, rubric criterion, constant, switch, sibling skill and cookbook section a bundle names still exists |
| `test_router.py` / `test_routing_property.py` | the four routing laws, by row and over the whole input space |
| `test_budget.py` | the index cost, re-measured against the shipped descriptions |
| `test_corpus.py` | evidence labels and corpus claims recomputed from `bench/out` |
| `test_telemetry.py` | the read probe, including the false-positive split |
| `test_packaging.py` | **a built wheel contains all 14 `SKILL.md`, all 14 `references/`, all 9 `_claims`** |
| `test_live_discovery.py` | §7 — a real CLI actually finds and opens a bundle |

Two contradiction checks are worth separating, because they answer different questions:

* **value-level**, library-wide: two skills may not point one claim key at different
  numbers. Compared on the *pre-scale value*, not the rendered text — `cv3d-bbox-contract`
  says "1 cm" and `cv3d-repeats-and-mirrors` says "0.01" metres, and both are right.
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
* **`cv3d-opengl-pipeline` claimed `measured` on n=5.** Now `mixed`.
* **The api-agent index cost 780 tokens for five skills** against a 300-token budget,
  because it quoted whole 1024-char descriptions. It quotes the first clause now (342
  tokens for the worst real session, 400 is the ceiling): our router already decided, so
  that index is a pointer, not a matcher.
* **The live smoke had never run.** It skipped every CLI because it looked for a binary
  named after the agent kind — `gemini-cli` runs `gemini`, `claude-code` runs `claude`.

---

## 6. Does it help? The A/B

<!-- AB-RESULT -->

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
  would have denied its own skill tool. Added; `3dcv doctor --skills` now checks it.
* gemini-cli's `activate_skill` consent prompt does not block us under
  `--approval-mode yolo`. This was the one claim that could not be verified statically.

**The surfaced-vs-deep split holds.** Controlled check: a bundle whose description does
*not* match the task, with the prompt "do not read any files, do not activate any skill" —
codex opened `SKILL.md` (its discovery scan reads the frontmatter) and left
`references/zebra.md` at `atime == mtime`. That is exactly the design: tier-1 scan bumps
SKILL.md, only a real read bumps `references/`.

---

## 8. How to add a skill

1. `codeverse/skills/<name>/SKILL.md` — `name` equals the directory name, lowercase
   `a-z0-9` and single hyphens; `description` (≤ 1024 chars) says WHAT and WHEN, because it
   is the only text a CLI matches on; **no `<` or `>` anywhere in the frontmatter** (the
   spec's prompt-injection rule); only spec keys at the top level, everything of ours under
   `metadata`; `metadata.evidence` and `metadata.verified` (a *quoted* ISO date) required.
2. `codeverse/skills/<name>/references/*.md` — **required**, one level. Without it the
   bundle can never score on the read metric.
3. `codeverse/skills/_claims/<name>.toml` — one row per number the body quotes from live
   code, so moving the constant breaks the test that ships the sentence:

   ```toml
   [[claim]]
   key    = "penetration_error_m"
   text   = "10 mm"                                              # must appear in the body
   python = "codeverse.spatial.connectivity:PENETRATION_ERROR_M"
   scale  = 1000
   format = "{:.0f} mm"
   ```
4. Add its rows to `ROUTES` in `codeverse/skills/registry.py`, in the same commit.
5. `3dcv skills validate --strict` and `pytest tests/skills`.

Body rules: ≤ 350 lines and ≤ 2,500 tokens; no code fence over 20 lines; never restate a
frame, unit or naming rule (`conventions.py` owns those); every corpus percentage carries
its battery, its n and its date.

---

## 9. Known open

* **Corpus percentages are checked for provenance, not recomputed.** A body's "47 graded
  blender runs" is a *slice* (one battery, one track, one round kind) and `bench/out` holds
  139 blender runs overall; without each claim declaring the query that produced it, a
  tight recomputation compares two different populations. The fix is to declare corpus
  queries the way `_claims/*.toml` declares constants. What is enforced today: a claim may
  not exceed what `bench/out` holds, `measured` needs n ≥ 20, and every rate names its
  battery / n / date.
* **The wider prompt corpus still contradicts the connectivity gate on weld overlap.**
  `contract.md` was fixed for all three languages, but `tracks/generate_static.j2`
  ("overlap neighbours by ≥ 0.002 m (push a leg 2-5 mm into the seat)"),
  `tracks/assemble_static.j2`, `generate_static_part.j2`, `system/harness_contract.md`
  ("seams overlap by ≥ 2 mm") and three cookbooks still teach 2–5 mm, which is at or above
  `PENETRATION_WARN_M`. That is a prompt-corpus change with its own measurement, not a
  skills change, and it is the single most likely reason a contact skill would fail to move
  the number it targets. **Do this before re-running the A/B.**
* **`ENV_RECIPES` / `ZONE_RECIPES` inlining in `tracks/scene.py` is untouched.** It is the
  control arm for the scene track's version of this question — up to 6,000 unconditional
  tokens per env/zone session — and has not been A/B'd against a routed skill.
* **api-agent read calibration is unmeasured.** Signal 3 (exact reads) exists and is unit
  tested, but no real battery has yet produced a session where it can be compared against
  the atime probe's answer.
* **`cadquery` and `threejs` bundles are routed off.** They stay off until each language
  reaches 20 graded runs.
