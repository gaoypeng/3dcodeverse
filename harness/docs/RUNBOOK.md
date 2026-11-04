# Runbook — operating the harness

Paths are relative to the harness root (`harness/` in the checkout) unless absolute.  The CLI is
`3dcodeverse` with short alias `3dcode` (used below).  Installing: `docs/INSTALL.md`.

## 1. Install and check

```bash
pip install -e harness      # once, from the checkout root; entry points 3dcodeverse and 3dcode
cd harness/runtime_js && npm install   # three@0.182, puppeteer (chrome cached)
3dcode doctor            # python deps, blender, node/three/puppeteer, chrome WebGL, keys, CLIs, git, mcp
3dcode doctor --live     # + one tiny Gemini call ("pong", ~$0.00001)
python -m pytest tests -q                              # offline suite; live tests are opt-in (blender/node/GL run when the binaries exist)
python -m pytest tests -q -m "not live and not blender and not node"   # pure-python subset
python -m pytest tests -q -m live                      # OPT-IN: real API calls
```

Gemini keys, the other providers' keys, the settings files, every `C3D_*` override, how Blender
is found and how the GPU path is chosen: `docs/INSTALL.md` §8, §6 and §7; each setting's default
and description is its `Settings` field in `codeverse3d/config.py`.

## 2. Running each track

```bash
# static object — Blender (default language, multi-file src/model.py + src/parts/*.py)
3dcode make "a mid-century wooden dining chair" --track static_object --language blender
3dcode make "a brass desk lamp" --track static_object --language cadquery
3dcode make "a classic park bench" --track static_object --language threejs --generator agy:gemini-4-argon
# articulated object — bpy links + URDF
3dcode make "a bedside cabinet with one hinged door and one drawer" --track articulated_object --language urdf_blender
# scene — multi-file three.js + GLSL (+ optional bpy GLB assets chosen by the planner), or bpy on Blender
3dcode make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs --rounds 2 --max-minutes 60
3dcode make "a small japanese garden at dusk with a koi pond" --track scene --language scene_blender --rounds 2 --max-minutes 90
# graphics — animated shader / raw OpenGL program
3dcode make "neon cyberpunk rain on a window with bokeh city lights" --track graphics --language glsl_shader
3dcode make "instanced pastel cubes with bloom" --track graphics --language opengl_python
```

Useful flags (`3dcode make --help`):

* `--generator`, `--planner`, `--judge` — backend ids (§3).
* `--image <png>` (repeatable) — reference images → `ReferenceJudge` + silhouette gate + IoU
  refine tasks.
* `--rounds N` — refine rounds after the baseline.  A run does exactly N, each built on the one
  before it, unless the clock or a hard failure stops it; there is no pass / plateau stop.
* `--candidates N` — best-of-N baseline: N parallel candidates in `<ws>/_cand/`, quick-judged,
  the highest quick score kept (fewer gate errors on a tie); multiplies baseline cost ≈ N;
  object and graphics tracks only (a scene writes its baseline in its stages).
* `--no-pick` — package nothing (no `deliverable/`, no `selection.json`; `3dcode pick` later).
* `--max-minutes`, `--dim height=0.45`, `--must`, `--must-not`, `--style`, `--tag`, `--seed` (a
  label: it re-keys the plan, nothing else is seeded by it), `--slug`, `--runs-dir`, `--force`,
  `--no-run` (workspace + spec.json only).
* `--variant NAME` — a per-model prompt/skill overlay (`codeverse3d/prompts/variants/<name>/` +
  `codeverse3d/skills/variants/<name>/`), frozen on `spec.options.variant` so `resume` and
  `judge` read the same files; default `C3D_VARIANT`, else `C3D_VARIANTS` keyed by the exact
  generator id, else the base prompts.  `--variant argon_raw` on `--language scene_blender` is
  RAW mode: no harness bpy library ships and the model writes every line of bpy.

Expected cost/time with gemini-3.8-flash: object tracks ≈ $0.7–0.9 and 12–36 min for baseline +
1 refine; best-of-2 single-shot ≈ $0.25 / 8 min; graphics single-shot ≈ $0.05 / 2 min per judged
round; scenes ≈ $2.3 and 30 min before the first judged round, then ≈ $0.36 / ~7 min per refine
(give scenes `--max-minutes 60`).  Actual spend: `3dcode cost <slug>`.

## 3. Backends

| id | what runs | notes |
|---|---|---|
| `agy:gemini-4-argon` (default) | `agy --dangerously-skip-permissions --print-timeout … --output-format json --add-dir ws --log-file trajectories/<label>_rNN/agy.log --model gemini-api://gemini-4-argon --effort high`, prompt on stdin | Gemini-API mode only (Gemini keys required, Gemini models only): one pool key per session, every spatial tool over MCP, priced at Gemini list price.  Truncates a prompt past ~175 kB itself |
| `single-shot:gemini:gemini-3.8-flash` | one structured-output call → multi-file envelope, no tools | fastest/cheapest; baseline for "raw model" deltas |
| `claude-code:<model>` | `claude -p --dangerously-skip-permissions --setting-sources project --settings '{"skillOverrides":…}' --mcp-config trajectories/<label>_rNN/mcp.json --strict-mcp-config …`, prompt on stdin, `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` | local subscription; no user setting source, so `~/.claude/settings.json` does not reach a session |
| `codex:<model>[@<effort>]` | `codex exec --json -C ws --sandbox workspace-write -c model_reasoning_effort=high -c skills.bundled.enabled=false -c mcp_servers.3dcode.… -`, prompt on stdin | local subscription; MCP tools need `default_tools_approval_mode="approve"` (the harness passes it); reasoning effort is always stated (`Settings.agents.codex_reasoning_effort`, default `high`; `codex:gpt-5.6-sol@medium` per id) |
| `gemini:* / anthropic:* / openai:*` | ChatModel for planner / judge / captioner / single-shot | Anthropic/OpenAI are mock-tested only |

How `agents/backends.py` runs agy (do not undo):

* API mode only: no subscription sign-in, no non-Gemini model; without keys `available()` is
  false and the session fails at once.  `agy` with no model = `gemini-4-argon`.  One key per
  session from the shared pool (`KeyPool.lease_session_key`: a machine-wide flock lease, so
  parallel runs put one session per key until every key is in one) as `GEMINI_API_KEY`, its
  outcome reported back (429 → cooldown, 401/403 → dead); other credential env stripped.
* Each session gets a fresh HOME under `<cache_dir>/agy_homes/…` holding
  `.gemini/antigravity-cli/settings.json` = `{"modelProvider": "gemini"}` (without it agy asks
  for a login) and `.gemini/config/mcp_config.json` with the `3dcode` server (agy has no
  per-workspace MCP).  The HOME is deleted after the session; the tool trace is read from its
  conversation db first.
* `--model gemini-api://<bare id> --effort <low|medium|high>`: the id goes to the API verbatim;
  effort from an agy-style `-low/-medium/-high` suffix on the generator id, else `high`.  An
  effort A/B is the id suffix: `--generator agy:gemini-4-argon-medium`.
* Cost: agy's `output_tokens` already include `thinking_tokens`, so output = output − thinking,
  thoughts = thinking (billed once).
* `--print-timeout` = `timeout_s` + the watchdog's grace + 1 min, so the watchdog decides; agy's
  own clock stopping a turn (`finished_reason: print_timeout`) is booked `exit_reason=timeout`.
* No retry loop in the backend: agy retries its own model calls; a transient death goes to the
  round loop's ONE transport retry, which takes a fresh key.
* Workspace files: `AGENTS.md` + `CLAUDE.md` (agy reads both) and `.antigravityignore`, which
  hides only noise (renders/judge output, `trajectories/`); build/census/measurement JSON stay
  readable.
* MCP server argv is `[sys.executable, -m, codeverse3d.spatial.mcp_server, --workspace, ws]`;
  server name `3dcode` → tools appear as `mcp__3dcode__<name>` (claude).  `3dcode mcp
  --workspace runs/<slug>` execs the same server; `python -m codeverse3d.spatial.mcp_server
  --workspace ws --list` prints the tools.

## 4. Where outputs land

Everything lands in `runs/<slug>/` — code in `src/` (git, one commit per round), each round's
build in `artifacts/rNN/`, and after a pick `deliverable/` + `selection.json`.  `3dcode status <slug>` prints the stop reason, baseline → picked score,
the rounds table, cost and the last events.

### Looking at results locally

```bash
3dcode gallery serve                       # ./runs + every eval/bench/out/*/runs → http://127.0.0.1:8765/
3dcode gallery serve ../eval/bench/out/static_v2_flash/runs --port 9000 --reload --no-open
3dcode gallery build --out gallery.html [--embed]      # one self-contained file (--embed inlines the sheets)
```

`serve` indexes the run roots and serves the run **directories** too, so every link works:
contact sheet, full-size renders, `record.json`, `plan.json`/`spec.json`, the `src/` tree,
`object.glb` (orbit viewer on the vendored three.js — no network), articulation sheets,
`preview.gif`, frames.  The page has a live summary strip (recomputed per filter), filters
(track / language / tier / backend / verdict / battery + text search) and sort that never
reload, a card ⇄ table toggle, light/dark, and a detail page per run (`/run/<battery>/<slug>`)
with every round, the picked round's verdict, the measurement table, the full render set, the
cost breakdown and the code.  Filters live in the query string, so a view can be curled or
bookmarked.  `--reload` re-scans on every page load (a run with no `record.json` yet shows as
*pending*, a half-written one as *broken*).  The server binds `127.0.0.1` unless you pass
`--host` and never serves a path outside the declared roots.

## 5. Resume, re-render, re-judge, export

```bash
3dcode resume <slug> [--candidates N] [--no-pick]
                                        # continues from run_state + stages/*.json and from the LAST round.  A
                                        # max_rounds run resumes only with a raised --rounds, a budget run with any
                                        # raised cap; an agent_quota, judge_unavailable or failed run resumes as is.
                                        # The budget SNAPSHOT is restored: active minutes already spent still count,
                                        # so a raised --max-minutes grants only the difference.  Once rounds exist a
                                        # recorded stage never runs again; `resume --force` regenerates the stages:
                                        # it archives the round journal + record.json to rounds/pre_force/ and the
                                        # run starts again at round 0
3dcode pick <slug> [--by score|pairwise] [--round N] [--judge MODEL]
                                        # hand over a round: deliverable/ + selection.json.  score = highest
                                        # effective score, ties → fewer gate errors → the earlier round; pairwise =
                                        # the pairwise judge between the top two when within 0.03; --round N = that
                                        # round.  `make`/`resume` run `pick --by score` for you
3dcode render <slug> [--mode shaded|wire|normals|clay|silhouette] [--out dir]
3dcode judge <slug> [--round N] [--rubric static_object] [--model gemini:gemini-3.1-pro-preview] [--n 3] [--thinking off|low|medium|high]
                                        # re-judges a round's recorded renders → artifacts/judge/rNN_cli.json
3dcode tools list [--cards] · 3dcode tools measure --workspace runs/<slug> · 3dcode tools gl_frames --workspace … --json '{"times":[0,1,2.5]}'
                                        # panel says ok | FAIL (the verdict) | error (the tool could not run); exit 1 on either
3dcode flywheel export runs/ dataset/ [--min-score 0.7] [--only-passed] [--pack] [--include-unbuilt]
                                     [--captions-dir caps/] [--drop-duplicates]
3dcode flywheel pairs runs/ pairs.jsonl [--min-delta 0.05]
3dcode flywheel refine runs/ refine.jsonl [--with-code]
                                     # one row per round the harness asked to change (addons/dataset/refine.RefineTransition);
                                     # training formats live in finetune/llamafactory/, not here
3dcode flywheel caption <slug> [--model …] [--out caps/]      # --out = side-car mode, run untouched
python -m codeverse3d.addons.calibration runs/<slug> [runs/<slug2> …] --model gemini:gemini-3.1-pro-preview --n 3 --out out/
                                        # re-judges recorded rounds; writes calibration_<model>.md/.json (never touches runs/)
```

A run that crashed outside its own handling leaves `record.json` with `status=failed`; `3dcode
resume` retries from the last completed stage/round (cached plan/skeleton/scene stages are
reused — this also recovers from a provider outage).  A refine session that dies on the
VENDOR's usage limit stops the run at `agent_quota`: every round so far is kept and picked from,
and `3dcode resume` continues it once the limit resets.  Ctrl-C and `kill <pid>` (SIGTERM) are
safe: `make` / `resume` kill every agent / build process group the run has open, record the
stop, and end within `proc.STOP_GRACE_S` (30 s).  Kill by PID, never by pattern: a
`pkill -f "3dcode make"` takes every other run on the box with it.

Two things to keep apart on one machine: run the offline test suite and a battery at different
times (both hold Blender, a browser pool and most cores; a red browser test under load is not
evidence until it reproduces on a quiet box), and give two concurrent batteries separate
`C3D_CACHE_DIR`s and half the `--parallel` each.  A worktree needs its own
`runtime_js/node_modules` (copy, do not symlink: `serve.cjs` refuses a real path outside its
root) and `PYTHONPATH=<worktree>/harness` before `3dcode make`, because the console script
imports the editable install, not the cwd.

## 6. Benchmarks

```bash
cd ../eval   # the battery launcher is the evaluation's
python -m bench.run_bench bench/prompts/static_objects_v1.yaml --generator single-shot:gemini:gemini-3.8-flash \
    --judge gemini:gemini-3.1-pro-preview --rounds 2 --parallel 4 [--tier easy] [--id furn_easy_stool] [--limit 6] [--out bench/out/x]
python -m bench.report bench/out/static_objects_v1      # report.md + self-contained report.html (gallery)
cd ../harness
python ../eval/bench/compare_backends.py --prompts ../eval/bench/prompts/compare_v1.yaml \
    --arms harness:agy:gemini-4-argon,oneshot:claude-code --judge gemini:gemini-3.1-pro-preview --out ../eval/bench/out/compare_v1
```

Results stream to `results.jsonl` (resumable).  The batteries, the protocol and judge
calibration: `eval/docs/EVAL.md`.  `compare_backends` preflights every model it needs and
refuses to start when one is not serving (`--wait-for-provider 60` parks until it recovers,
`--no-preflight` skips the check); cells a provider outage kills are recorded `infra_failed`,
excluded from every rate, and re-run with `--redo-status infra_failed`.  `run_bench
--redo-status` starts the redo fresh (the old tree is archived as `runs/<id>.attempt<N>`).  Never
start a second `run_bench` on an out dir with a live driver: both would run the same prompt.
Under a provider storm, launch with `--max-minutes 120 --wait-for-provider 60` and redo the
storm's rows (`--redo-status error,infra_failed,budget`) rather than reading them.

## 7. Extending (plugin paths)

* **New language**: enum in `contracts/common.py::Language` (+ `TRACK_LANGUAGES`, `ENTRY_FILE`,
  `LANGUAGE_LABEL`), frame in `conventions.LANGUAGE_FRAME`; `languages/<lang>/__init__.py`
  implementing `LanguageRuntime` — its file layout included: subclass
  `languages/base.RuntimeLayout` (entry + `extra_files`, and `part_file` for a one-file-per-part
  language) or answer `expected_files(plan)` / `files_for(plan, target)` yourself; the tracks
  never restate it.  A python-executed build wrapper goes beside the others in
  `languages/wrappers/` (import `_wrapper_common`; run it with `_common.run_wrapper_build`, or
  read its report with `_common.compose_build_result`); branch in `languages/base.py::get_runtime`;
  `prompts/<lang>/{system,contract}.md` (found by `prompts/catalog.language_prompt`; no cookbook
  — the prompts state requirements, never templates: no code block over ~12 lines, numbers only
  where a gate or the judge measures them, and contract.md carries ONE executable interface
  example that `tests/prompts/test_snippets_run.py` runs through the real runtime).
* **Changing what a model is shown** (a prompt file or template variable, a contract, a skill, a
  rubric's text, a tool description, anything a session or a judge reads):
  `tests/prompts/test_manifest.py` pins every payload of offline scenarios across the four
  tracks and every language — agent sessions, chat calls, each judge's input, the materialised
  workspace files, the routed skills, the MCP tool lists — and every stage's cache key.  A
  refactor leaves it byte-identical; a change a model is meant to see re-blesses it in the same
  commit, saying why: `python -m tests.prompts.manifest --bless` (`--dump DIR` writes every
  payload, to `diff -r` two trees).  A new prompt file must render through the loader
  (`tests/prompts/test_files.py`); a new language or track adds its scenario to `SCENARIOS`.
  Every new template variable is guarded `{% if name is defined and name %}` for at least one
  wave: templates are read at render time, so a live worker on old python renders the new file.
* **A per-model variant**: `codeverse3d/prompts/variants/<name>/variant.toml` (`[variant]` name =
  the directory name, version, generators, description; optional `[knobs]` — `skills_max`,
  `skills_only`, `scene_lib` — applied as defaults, a user-stated field wins) beside the prompt
  files that shadow the base ones by relative path (a template there may `include` a base
  file), and `codeverse3d/skills/variants/<name>/` for bundles that shadow base bundles by name,
  their `_claims/`, and `routes.toml` (`[[boost]]` skill + delta, `[[disable]]` rule, `[[route]]`
  extra typed rows with fresh ids).  Nothing else changes: `prompts.load_text` / `render` /
  `catalog.language_text` and `skills.all_skills` resolve through the active variant, the record
  carries `extra.variant` {name, version, hash} + `prompt_hashes.variant`.  An overlay starts
  EMPTY (the shipped `argon` is) so its first iteration measures the base; the manifest gets one
  scenario per variant.  Selection is explicit — `--variant`, `C3D_VARIANT`, `C3D_VARIANTS` —
  never a pattern over the model id.
* **New spatial tool**: pydantic args + `@tool("name", Args, "…", tracks=(…), languages=(…),
  cost_hint=…)` in `spatial/tools.py`; available to tracks, MCP and prompt cards at once.  Update
  `tests/spatial_tools` EXPECTED_TOOLS.
* **New rubric**: `judges/rubrics/<name>.yaml` with `pass_threshold`, `criteria[{id, weight,
  floor, title, description, anchors, kind}]`, `caps[{id, cap, when:
  gate|acceptance|console|missing_views, gate, severity, kinds}]`, `defects[{id, text, penalty,
  cap}]`.  Tracks pick rubrics in `tracks/*.py`; `3dcode judge` maps track → rubric in
  `cli/_judge.py::rubric_for`.
* **New backend**: ChatModel → `models/<provider>.py` + registry + prices; CodingAgent →
  `agents/backends.py` using `cli_common` + registry + `materialize.py`.
* **New track**: subclass `tracks/lifecycle.py::BaseTrack` (its pre-round graph `stages`, a
  tuple of `StageNode`s — the default is skeleton → materialize, and a new track adds its line to
  `docs/ARCHITECTURE.md` §7.1; hooks: `make_pipeline`, `baseline_tasks`, `refine_tasks`;
  `system_prompt` defaults to `language_system_prompt(ctx.language, tools=not ctx.single_shot)`
  and `refine_file_for_target` to the runtime's `files_for`), a `RoundPipeline`, plan model in
  `contracts/plan.py`, `.j2` prompts, the planner's per-track rows in `tracks/planner.py`,
  branch in `tracks/__init__.py`.  `tracks/graphics.py` is the template for a track with no GLB.
* **New bench battery**: `eval/bench/prompts/<name>.yaml` with `name, track, language,
  prompts[{id, tier, category, prompt, must_have, dimensions_m}]`.
