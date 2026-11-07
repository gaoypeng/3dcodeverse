# Skills

A **skill** is a task-scoped rule sheet the harness attaches to a coding session automatically —
from the track, the language, the round kind, the plan and **the previous round's gate
findings**.  Nobody writes `skills: [...]`.

It is not a recipe library.  No language ships a cookbook: interface and language facts live in
`contract.md` / `system.md` (one executable example each), and a skill is a sheet of measured
requirements with a few SHORT optional practices — real API names, numbers only where a gate or
the judge measures them, no code block over ~12 lines.

`SKILL.md` is an open standard ([agentskills.io](https://agentskills.io/specification)), so the
format is not ours to bend — and it is why the bundles reach a session only through its vendor's
native skill loader: claude-code, codex and agy discover them themselves.

`C3D_SKILLS` is **on by default**; `C3D_SKILLS=0` is the off switch and an A/B's no-skills arm.
Knobs: `C3D_SKILLS_ONLY` (restrict the library before routing), `C3D_SKILLS_DIR` (another library).

## 1. How the library is managed

**One library, one policy, thin adapters.**  The bundles under `codeverse3d/skills/<name>/` are
plain `SKILL.md` directories that know nothing about any backend — `cp -r` one into
`~/.claude/skills/` and it works without the harness.  `registry.py` picks the set from typed
inputs.  The one thing that differs between coding agents is the discovery root, in one file,
`skills/prompting.py` (`_BACKEND_ROOTS`):

| backend | discovery root | how the CLI shows and activates a bundle |
|---|---|---|
| `claude-code` | `.claude/skills` | a name + description list (the stream-json `init` event's `skills`); `Skill {"skill": name}`, then `Read` of `references/` |
| `codex` | `.agents/skills` | a `## Skills` section of the prompt; a shell `sed`/`cat` of `SKILL.md` |
| `agy` | `.agents/skills` | a skills section of its system prompt; `read_file` of `SKILL.md` |

The harness writes the bundles under the discovery root and hands the agent no second index
and no tool; the body file gets one MANDATORY sentence.

**A session sees nothing but the routed bundles.**  Every CLI lists skills of its own; each is
switched off with the CLI's documented knob (`agents/backends.py`): claude-code with
`--setting-sources project` + `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` + `--settings
{"skillOverrides": …}` (so the user's `~/.claude/settings.json` does not reach a session
either), codex with `-c skills.bundled.enabled=false` (a user root such as `~/.codex/skills` has
no switch), agy not at all (its 5 built-ins are always listed).

## 2. The library

Twenty bundles; the caps are 350 body lines and 2,500 tokens.  `evidence` is provenance — where
the prose came from — not a measured effect.

| skill | track | language | evidence | routes |
|---|---|---|---|---|
| `c3d-part-contact` | object tracks | any | measured | R1, R2 |
| `c3d-bbox-contract` | object tracks | any | measured | R3, R4 |
| `c3d-repeats-and-mirrors` | object tracks | any | measured | R6, R7 |
| `c3d-blender-forms` | any | blender, urdf_blender | measured | R8, R9 |
| `c3d-urdf-joints` | articulated_object | urdf_blender | measured | R12, R13 |
| `c3d-scene-composition` | scene | scene_threejs | mixed | R14, R15, R34 |
| `c3d-scene-lighting` | scene | scene_threejs | mixed | R16, R17 |
| `c3d-scene-motion` | scene | scene_threejs | mixed | R18, R19 |
| `c3d-threejs-shader-traps` | scene (+ static on a preflight finding) | threejs both | mixed | R20, R21 |
| `c3d-glsl-craft` | graphics | glsl_shader | mixed | R22, R24-glsl |
| `c3d-opengl-pipeline` | graphics | opengl_python | mixed | R23, R24-opengl |
| `c3d-cadquery-forms` | any | cadquery | inherited-unverified | R10 |
| `c3d-threejs-forms` | static_object | threejs | inherited-unverified | R11 |
| `c3d-scene-atmosphere` | scene | scene_threejs | inherited-unverified | R25 |
| `c3d-scene-water` | scene | scene_threejs | inherited-unverified | R26 |
| `c3d-scene-night` | scene | scene_threejs | inherited-unverified | R27 |
| `c3d-scene-materials` | scene | scene_threejs | inherited-unverified | R28 |
| `c3d-bpy-scene-placement` | scene | scene_blender | mixed | R29, R30 |
| `c3d-bpy-scene-lighting` | scene | scene_blender | mixed | R31, R32 |
| `c3d-bpy-scene-materials` | scene | scene_blender | mixed | R33 |

All twenty route by default, capped at 5 a session.  To measure a subset, `C3D_SKILLS_ONLY`
restricts the library before routing, against a `C3D_SKILLS=0` arm.  Route id **R5** is retired
and never reused: a run record refers to a routing decision by id
(`tests/skills/test_registry.py::RETIRED_RULES`).

Every bundle also declares the ONE deterministic quantity it claims to move (`target_metric` /
`target_direction` / `target_baseline` in its frontmatter and `addons/skill_targets.py`;
`python eval/bench/skill_targets.py eval/bench/out` prints them, per battery or paired across an
A/B).

## 3. Routing

`codeverse3d/skills/registry.py` holds the typed table (ids R1–R34) and `finding_kind()`, which
names a finding `<gate>/<data.kind>` — the kind the gate wrote, never its message; `select()`
turns `(track, language, kind, plan signals, findings)` into a ranked, capped, reasoned set.
Four laws, all tested:

1. **Gate findings outrank everything.**  A row answering a finding sits at priority ≥ 90,
   standing rows below 90: a repair round spends its budget on what broke — the input no CLI's
   own loader can see, and the reason routing is in code.
2. **The cap is 5** (`select(max_skills=)`); it cuts the low-priority tail, never the gate-fired head.
3. **Quiet kinds attach nothing on their own** — `asset`, `asset_fix`, `reference` are short,
   narrow sessions.  A gate-fired row still reaches them.
4. **Unknown input degrades to `[]`, never an exception** — a planner failure must not also take
   out the round's skills.

Every gate writes a `data.kind` — lints from `contracts.artifacts.LintKind`, connectivity
`stray_islands` / `no_ground`, motion_direction `wrong_direction` / `skipped`, render_console
`console_error`, scene_probe `draw_calls` — so R9 answers `lint/part_not_imported`, not every
lint line; INFO findings have no kind.  A bundle is added with its rows in the same commit — an
unroutable bundle pays the index and never helps, and a test fails on both halves.

## 4. Telemetry

Per round, `RoundRecord.skills` is a `SkillsUsage`: listed, surfaced, read deep, `index_tokens`,
`body_tokens_read`, `evidence` and the control's state.  Each attached body's hash goes into
`record.prompt_hashes` as `skill:<name>`; per session, `telemetry/skills.jsonl` gets one line.

Ground truth is the CLI's own record: each backend writes what its CLI says it called into
`trajectories/<label>/transcript.jsonl` (claude-code's stream-json, codex's `exec --json`
events, agy's conversation database read before its HOME is deleted), and
`skills/telemetry.probe_reads` credits a bundle as *surfaced* when a call activated it or named
its `SKILL.md`, *deep* when a call named a file under its `references/`.  The atime probe is the
fallback for a session that left no trace, guarded by a never-routed **control** bundle:
when the control comes back opened (git reads untracked files too), the session's deep-read rate
is `None`, not a number.

```
3dcodeverse skills report eval/bench/out/<battery>    # deep-read rate per skill per backend (--json)
3dcodeverse skills validate [--strict]                # bundles, claims
```

Target: ≥ 60 % deep-read, counted only over sessions the control says were measurable.  A skill
under 20 % over 20 such sessions is merged or deleted — a library that only grows ends as bloat.

## 5. How to add a skill

1. `codeverse3d/skills/<name>/SKILL.md` — `name` equals the directory name (lowercase `a-z0-9`,
   single hyphens); `description` (≤ 1024 chars) says WHAT and WHEN — the only text a CLI
   matches on; no `<` or `>` in the frontmatter; spec keys only at the top level, everything of
   ours under `metadata`, where `evidence` and `verified` (a *quoted* ISO date) are required.
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
   line it must stand on (`context = "WARN below"`).  The prompt corpus pins its numbers the
   same way in `codeverse3d/prompts/_claims.toml` (`check_prompt_claims`, run by `3dcodeverse skills
   validate`).
4. Its rows in `ROUTES` (`codeverse3d/skills/registry.py`), in the same commit.
5. A `Target` row in `codeverse3d/addons/skill_targets.py` and the matching `target_*`
   frontmatter keys — the ONE deterministic quantity the bundle claims to move, its direction,
   and its baseline; if no deterministic instrument can see the claim, say so with
   `measurable=False` and a `caveat`.
6. `3dcodeverse skills validate --strict` and `pytest tests/skills`.

Body rules: ≤ 350 lines and ≤ 2,500 tokens; no code fence over 12 lines; never restate a frame,
unit or naming rule (`conventions.py` owns those); every corpus percentage carries its battery,
its n and its date.  `tests/skills/` enforces all of it: the spec's field rules, the body budget,
every `_claims` number against its live constant, the four routing laws, and the read probe.
