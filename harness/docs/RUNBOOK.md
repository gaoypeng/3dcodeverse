# Runbook — operating the harness

Everything here was exercised on this machine (WSL2, RTX 5090, Blender 5.0.1,
node 24, gemini-cli 0.53, claude 2.1, codex 0.147+, agy 1.1) on 2026-08-23.  Paths
are relative to `/home/yipeng/3dcodeverse/harness` unless absolute.  The CLI is
`3dcodeverse` with short alias `3dcv` (used below).

## 1. Install and check

```bash
pip install -e /home/yipeng/3dcodeverse/harness      # once; entry points 3dcodeverse and 3dcv
cd /home/yipeng/3dcodeverse/harness/runtime_js && npm install   # three@0.182, puppeteer (chrome cached)
3dcv doctor            # python deps (incl. python-fcl, moderngl), blender, node/three/puppeteer, chrome WebGL, keys, CLIs, git, mcp
3dcv doctor --live     # + one tiny Gemini call ("pong", ~$0.00001)
python -m pytest tests -q                              # offline suite; live tests are opt-in (blender/node/GL run when the binaries exist)
python -m pytest tests -q -m "not live and not blender and not node"   # pure-python subset
python -m pytest tests -q -m live                      # OPT-IN: real API calls
```

### Keys and settings
* Gemini keys, in precedence order: `GEMINI_API_KEYS` (comma-separated) → `GEMINI_API_KEY`
  / `GOOGLE_API_KEY` → legacy `~/.config/astra3d/gemini_keys.env` (22 keys here).
  All `gemini:*` models share one `KeyPool` (900 rpm/key, 30 s cooldown on 429; dead
  keys benched 1 h and re-probed; 429s rotate to fresh keys for free).
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CV3D_OPENAI_BASE_URL` for the other
  providers (not set on this box → `anthropic:*` / `openai:*` unavailable).
* Settings: `~/.config/codeverse/config.yaml` or `./codeverse.yaml`, overridden by env
  with prefix `CV3D_` and `__` nesting — e.g. `CV3D_RUNS_DIR=/data/runs`,
  `CV3D_RENDER__GPU=off`, `CV3D_BINARIES__BLENDER=/opt/blender/blender`,
  `CV3D_LIMITS__AGENT_TIMEOUT_S=900`, `CV3D_DEFAULT_CANDIDATES=2`.
  Defaults: runs_dir `runs/`, cache_dir `~/.cache/codeverse`, build/render timeout 300 s,
  agent timeout 1800 s, bpy RLIMIT 12 GB, 6 parallel agents, 3 parallel builds,
  `default_judge gemini:gemini-3.1-pro-preview`, `default_candidates 1`.
* Blender: `settings.binaries.blender` else first `blender-5.0`/`blender`/… on PATH.
  Always `-b --factory-startup`; the child env strips `PYTHONPATH`/`PYTHONHOME`.
* GPU rendering: `runtime_js/gpu_launch.cjs` tries Chrome with `--use-angle=gl-egl` +
  Mesa d3d12 env and falls back to SwiftShader; probe cached in
  `~/.cache/codeverse/gpu_probe.json`.  Force with `CV3D_RENDER_GPU=on|off|auto` or
  `settings.render.gpu`.  The graphics track uses moderngl the same way (d3d12 GPU
  context first, llvmpipe fallback; no probe needed — `GlHost` decides per process).

## 2. Running each track

```bash
# static object — Blender (default language, multi-file src/model.py + src/parts/*.py)
3dcv make "a mid-century wooden dining chair" --track static_object --language blender
3dcv make "a brass desk lamp" --track static_object --language cadquery
3dcv make "a classic park bench" --track static_object --language threejs --generator gemini-cli:gemini-3.7-flash
# articulated object — bpy links + URDF
3dcv make "a bedside cabinet with one hinged door and one drawer" --track articulated_object --language urdf_blender
# scene — multi-file three.js + GLSL (+ optional bpy GLB assets chosen by the planner)
3dcv make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs --rounds 2 --max-minutes 60
# graphics — animated shader / raw OpenGL program
3dcv make "neon cyberpunk rain on a window with bokeh city lights" --track graphics --language glsl_shader
3dcv make "instanced pastel cubes with bloom" --track graphics --language opengl_python
```
Useful flags (`3dcv make --help`): `--generator`, `--planner`, `--judge`, `--captioner`
(backend ids, §3), `--image <png>` (repeatable; reference images → `ReferenceJudge` +
silhouette gate + IoU refine tasks), `--rounds N` (refine rounds after the baseline),
`--candidates N` (best-of-N baseline: N parallel candidates in `<ws>/_cand/`,
quick-judged, pairwise tie-break, winner kept; multiplies baseline cost ≈ N;
default from `settings.default_candidates`), `--texture` (run the texture pass after
finalise; see §6), `--max-minutes`, `--dim height=0.45`, `--must`,
`--must-not`, `--style`, `--tag`, `--seed`, `--slug`, `--runs-dir`, `--force`,
`--no-run` (workspace + spec.json only — except that `--reference` still runs its
paid grounding pass first, since the grounded spec is what it writes).

Expected cost/time with gemini-3.7-flash: object tracks ≈ $0.7–0.9 and 12–36 min for
baseline + 1 refine; best-of-2 single-shot ≈ $0.25 / 8 min; graphics single-shot
≈ $0.05 / 2 min per judged round; scenes ≈ $2.3 and 30 min before the first judged
round, then ≈ $0.36 / ~7 min per refine (give scenes `--max-minutes 60`).

## 3. Backends

| id | what runs | notes |
|---|---|---|
| `gemini-cli:gemini-3.7-flash` (default) | `gemini -p … --approval-mode yolo --skip-trust --output-format json`; every spatial tool over MCP | cheapest agentic path; transcripts feed repair-pair mining; see gotchas below |
| `single-shot:gemini:gemini-3.7-flash` | one structured-output call → multi-file envelope, no tools | fastest/cheapest; baseline for "raw model" deltas |
| `claude-code:<model>` | `claude -p … --dangerously-skip-permissions --mcp-config trajectories/<label>_rNN/mcp.json --strict-mcp-config …` | local subscription — test lightly |
| `codex:<model>[@<effort>]` | `codex exec --json -C ws --sandbox workspace-write -c model_reasoning_effort=high … -c mcp_servers.3dcv.…` | subscription; MCP tools need `default_tools_approval_mode="approve"` (harness passes it); reasoning effort is always stated (`Settings.agents.codex_reasoning_effort`, default `high`; `codex:gpt-5.6-sol@medium` per id, `""` to defer to `~/.codex/config.toml`) |
| `agy:<model>` | `agy --print … --add-dir ws` | no per-workspace MCP: tools via `3dcv tools <name> --json … --workspace .`; no served-model or cost reporting |
| `gemini:* / anthropic:* / openai:*` | ChatModel for planner / judge / captioner / single-shot | Anthropic/OpenAI untested live here |

### gemini-cli gotchas (handled by `agents/backends.py`; do not undo)
* System settings file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH`: api-key auth,
  `experimental.dynamicModelConfiguration=true` (else unknown models are silently
  substituted → checked, `exit_reason=model_substituted`), `security.folderTrust.enabled=false`
  (else workspace MCP servers are silently ignored even with `--skip-trust`).
* Workspace `.gemini/settings.json` sets `context.fileFiltering.respectGitIgnore=false`
  so the fine-grained `.geminiignore` (not the git ignore) decides what the agent can
  read: build/census/measurement JSON and its own `task_prompt.md` stay readable,
  renders/judge output/transcripts stay hidden.
* One pool key injected as `GEMINI_API_KEY`; other credential env stripped; retries
  prefer a different key (never raise KeyPoolExhausted out of a session).
* Cost accounting: `tokens.prompt` (total, incl. cached) is the input count; each
  served model priced at its own rate.
* Cookbook copied to `ws/.3dcv/cookbook.md` (gemini-cli cannot read outside the ws).
* MCP server argv is `[sys.executable, -m, codeverse.spatial.mcp_server, --workspace, ws]`;
  server name `3dcv` → tools appear as `mcp_3dcv_<name>` (gemini) / `mcp__3dcv__<name>`
  (claude).  `3dcv mcp --workspace runs/<slug>` execs the same server;
  `python -m codeverse.spatial.mcp_server --workspace ws --list` prints the tools.

## 4. Where outputs land

`runs/<slug>/` (ARCHITECTURE §3).  Code in `src/` (git; one commit per round), built
artifacts in `artifacts/` (`object.glb`, `robot.urdf` + `meshes/`, scene
`public/assets/*.glb`, graphics `frames/` + `frames_sheet.png` + `preview.gif` +
`metrics.json`, texturing `object_textured.glb` + `textures/`), per-round renders in
`artifacts/renders/rNN/`, gate JSON in `artifacts/gates/rNN/`, verdicts in
`artifacts/judge/rNN.json`, transcripts in `trajectories/<label>_rNN/` (retries in
`<label>.a2_rNN`), events in `events.jsonl`, the flywheel record in `record.json`.
`3dcv status <slug>` prints the rounds table (best round starred), cost and the last
events.

### Looking at results locally

```bash
3dcv gallery serve                       # ./runs + every ./bench/out/*/runs → http://127.0.0.1:8765/
3dcv gallery serve bench/out/static_v2_flash/runs --port 9000 --reload --no-open
3dcv gallery build --out gallery.html [--embed]      # one self-contained file (--embed inlines the sheets)
3dcv flywheel gallery runs/ gallery.html             # alias of `gallery build --embed` (old signature)
```
`serve` indexes the run roots (records only — ~85 runs in ~0.15 s) and serves the run
**directories** too, so every link works: contact sheet, full-size renders, `record.json`,
`plan.json`/`spec.json`, the `src/` tree (browsable, line-numbered, `raw` = text/plain),
`object.glb` (orbit viewer on the vendored three.js — no network), articulation sheets,
`preview.gif`, frames, textures.  The page has a live summary strip (n, pass rate,
mean/median score, total $, $ per passing artifact, wall clock — **recomputed per filter**),
filters (track / language / tier / backend / verdict / battery + text search over
prompt+slug) and sort (score / cost / time / name) that never reload, a card ⇄ table
toggle, light/dark, and a detail page per run (`/run/<battery>/<slug>`) with every round,
the best round's judge verdict + issues + improvement plan, the measurement table, the
full render set, the cost breakdown and the code.

Filters live in the query string, so a view can be curled or bookmarked:
`curl -s 'http://127.0.0.1:8765/?track=graphics&tier=A&sort=cost'`, and
`/api/runs?...` / `/api/summary?...` return the same selection as JSON.
`--reload` re-scans on every page load, which is what you want while a bench is writing:
a run with no `record.json` yet shows as a *pending* card and a half-written one as
*broken*, never as a crash.  The server binds `127.0.0.1` and refuses any other address
unless you type `--host` yourself; it never serves a path outside the declared roots.

## 5. Resume, re-render, re-judge, texture, export

```bash
3dcv resume <slug> [--candidates N]     # continues from run_state + stages/*.json (input-hash cached).  The budget
                                        # SNAPSHOT is restored: money/calls/active-minutes already spent still count,
                                        # so a raised --max-minutes grants only the difference (downtime never counts)
3dcv render <slug> [--round N] [--mode shaded|wire|normals|clay|silhouette] [--out dir]
3dcv judge <slug> [--round N] [--rubric static_object_v1] [--model gemini:gemini-3.1-pro-preview] [--n 3]
                                        # re-judges a round's recorded renders → artifacts/judge/rNN_cli.json
3dcv texture pass <slug> [--no-judge] [--model …] [--judge-model …] [--image-model …] [--size 1024]
3dcv texture scene-pack <slug> [--n 10] · 3dcv texture show <slug>
3dcv tools list [--cards] · 3dcv tools measure --workspace runs/<slug> · 3dcv tools gl_frames --workspace … --json '{"times":[0,1,2.5]}'
                                        # panel says ok | FAIL (the verdict) | error (the tool could not run);
                                        # exit 1 on either non-ok state — `scene_probe` included since 2026-09-02
                                        # (COST.md §30), where a failing scene gate used to exit 0
3dcv flywheel export runs/ dataset/ [--min-score 0.7] [--only-passed] [--pack] [--include-unbuilt]
                                     [--captions-dir caps/] [--drop-duplicates]
3dcv flywheel pairs runs/ pairs.jsonl [--min-delta 0.05]
3dcv flywheel refine runs/ refine.jsonl [--with-code]
                                     # one row per round the harness asked to change; the row
                                     # schema is codeverse/flywheel/refine.RefineTransition and
                                     # INTERFACES has the call signatures.  Training formats live
                                     # in toolkits/llamafactory/, not here.
3dcv flywheel caption <slug> [--model …] [--out caps/]      # --out = side-car mode, run untouched
3dcv flywheel gallery runs/ gallery.html [--title …]        # alias of `3dcv gallery build --embed` (§4)
3dcv flywheel index runs/ runs_index.sqlite · 3dcv flywheel dedupe dataset/
python -m codeverse.judges.calibration runs/<slug> [runs/<slug2> …] --model gemini:gemini-3.1-pro-preview --n 3 --out out/
                                        # re-judges recorded rounds; writes calibration_<model>.md/.json (never touches runs/)
```
**Do not run the offline suite while a battery is running.**  The suite is 2 300+ tests on
24 cores under xdist; a battery holds Blender, a browser pool and several agent sessions.
Twice on 2026-09-04/05 that produced 11 and 21 spurious failures that vanished on a clean
re-run (`tests/scene_runtime/lib/*` first, since those hold the renderer longest).  A red
suite during a battery is **not evidence until it reproduces on a quiet machine** — re-run
it there before pushing, and if the same tests are red again, it is real.  The mechanism is
the one the next paragraph describes: `tests/scene_runtime/lib/*` reach the battery's
browser daemon through a shared `CV3D_CACHE_DIR`, so giving the suite its own cache dir may
let the two coexist.

The same holds for a box loaded by OTHER work.  On 2026-09-05, with 12 `proseg` processes
holding 528 GB, swap full and all eight GPUs at ~100 %, the offline suite failed 12, then
44, then 17 tests in three runs at `-n 12 / 6 / 4`, every one of them a browser test
reporting `Session closed` or `Target closed`; `tests/scene_runtime` alone at `-n 4` with
its own cache dir passed 787/787.  Check `uptime` and `free -g` before believing a red
browser test.  One test is flaky by construction under CPU contention and is NOT to be
"fixed" by weakening it: `test_studio_render_is_reproducible_and_stamps_the_rig_version`
compares two renders byte for byte, and SwiftShader is not bit-reproducible when the box
is busy — it failed 2 runs in 3 on an unmodified tree at load 129.  A second one behaves
the same way: `test_example_scene_passes_frame_gate_and_judge_subset` asserts that a
frame's content / ground / sky coverage fractions sum to 1 +- 0.02, and at `-n 4` under
load ~100 it fails on `content 0.644 + ground 0.000 + sky 0.549 = 1.193`.  An interleaved
A/B — six runs each, alternating so both arms see the same load — gave **5/6 failures on
the unmodified tree and 4/6 with the branch's changes**, i.e. the box, not the code.  What
produces a ground fraction of exactly zero on a scene that has a ground is an open
question; it needs an idle machine to look at, not a guess.  A third is a plain wall-clock
assertion: `test_cabinet_door_end_to_end` requires the URDF build to finish in under 30 s
and measured 38 970 ms at load ~100 with `ok=True` — the build succeeded, the box was
busy.  All three are the same story, and none of them is to be "fixed" by loosening what
it asserts.

**Two arms at once need separate `CV3D_CACHE_DIR`, not just separate `--out`.**  The
browser daemon advertises its endpoint in `CACHE_DIR/browser_<backend>.json` and a newer
daemon supersedes an older one (`runtime_js/browser_daemon.cjs`), so two worktrees sharing
a cache fight over ONE browser, and `render_glb` returns "produced no result" on one side
only, looking like random flakiness (2026-09-04).  The mechanism is not confirmed: each
render serves its GLB from its own process (`render_glb.mjs` `serveDirs`), so it is not a
server-root mismatch; the likely cause is the superseded daemon exiting while the other
arm's render is still in flight on its browser, which is a race in shared code that a
separate cache dir avoids rather than fixes.  Give each arm its own cache dir; the render
cache separates with it, which an A/B wants anyway.

Also halve each arm's `--parallel`: two arms at 3 workers is six concurrent renders, and a
round with no renders skips the judge, so the loop stops at `judge_unavailable` and the
cell is finished with no score (`bench run` will not re-run it: the row exists).  Redo the
scoreless cells afterwards — delete the row from `results.jsonl` and the run directory,
then `bench run --id <prompt>`.

**A new worktree needs `runtime_js/node_modules` before it can run a battery.**  Without it
`render_glb` dies on every round, the judge is skipped for want of renders, and the cells
come back `status=plateau` with `score=None` — an arm that reads as healthy and measures
nothing (2026-09-04, the mimic-off arm).  `npm ci` in `runtime_js/`, or symlink the
directory from a worktree that has it (the `package.json` is the same file).  The row now
says `no verdict in any of N round(s)` when this happens.

A run that crashed outside its own handling leaves `record.json` with `status=failed`;
`3dcv resume` retries from the last completed stage/round (cached plan/skeleton/scene
stages are reused — this also recovers from Gemini 503 storms).  Ctrl-C is safe.

## 6. Texture pass

`3dcv texture pass <slug>` (or `--texture` on `make`): one VLM material plan →
tileable texture images (gemini-3.1-flash-image, ~$0.07/tile, cached by prompt) →
world-metre UVs → `artifacts/object_textured.glb` → seam gate + before/after judge
gate (ships only when the score does not drop and the materials criterion improves).
`record.extra["texturing"]` holds shipped/delta/cost; `3dcv texture show <slug>`
prints it.  Scenes: `3dcv texture scene-pack <slug>` writes 6–12 named tiles +
manifest under `public/textures/` for zone prompts.  Object tracks' agents can also
call the `texture_pass` / `texture_preview` tools mid-session.

## 7. Benchmarks

```bash
3dcv bench run bench/prompts/static_objects_v1.yaml --generator single-shot:gemini:gemini-3.7-flash \
    --judge gemini:gemini-3.1-pro-preview --rounds 2 --parallel 4 [--tier easy] [--id furn_easy_stool] [--limit 6] [--out bench/out/x]
3dcv bench report bench/out/static_objects_v1      # report.md + self-contained report.html (gallery)
python bench/compare_backends.py --prompts bench/prompts/compare_v1.yaml \
    --arms harness:gemini-cli:gemini-3.7-flash,oneshot:claude-code --judge gemini:gemini-3.1-pro-preview --out bench/out/compare_v1
```
Results stream to `results.jsonl` (resumable).  Batteries: `static_objects_v1` (24),
`articulated_v1` (12), `scenes_v1` (12), `compare_v1` (8, harness-vs-one-shot).
Protocol and judge calibration: `docs/EVAL.md`.

`compare_backends` **preflights every model it needs** (one ~20 s probe each) and
refuses to start when one is not serving — a dead provider does not fail fast on its
own, it lets each cell burn its full retry budget first.  `--wait-for-provider 60`
parks until it recovers instead; `--no-preflight` skips the check.  Cells that a
provider outage kills anyway are recorded `infra_failed`, excluded from every rate,
and re-run with `--redo-status infra_failed` (see `docs/EVAL.md` §7).

### 7.v Templates are read at render time — a new required variable breaks live workers like a moved name

Measured 2026-08-26 13:45: the fewer-turns landing added `{{ turn_discipline }}` to four static
templates; the four h2h drivers started 50 minutes earlier still ran the old python (no such
context key) but rendered the NEW template from disk → `UndefinedError: 'turn_discipline' is
undefined`, four prompts recorded `error` at 0 min.  Rule: every new template variable is
guarded `{% if name is defined and name %}` for at least one wave, and the "Landing source
changes while a wave is running" rule covers `codeverse/prompts/**` as well as moved names.

### 7.u `bench run --redo-status` starts the redo fresh (since c828637)

Before it, a redo resumed the old workspace — old `spec.json` (the old `max_minutes`) and the
old clock — so a `budget` row redone with `--max-minutes 120` was over budget before its first
round (clock_q4, lighthouse_1: `budget`, 0 rounds, "60.3 / 76.7 min elapsed").  Now the old
tree is archived as `runs/<id>.attempt<N>` and the prompt runs from scratch with the new
options, the way `ab_plan` has done since the skills wave.

It is also the ONLY way: `bench run --no-resume` was deleted 2026-08-30.  It dropped the
recorded rows and then resumed the workspace anyway — `resume = ws.exists()` never read the
flag — so a "fresh" rerun carried the old spec, rounds, spend and clock and appended a second
results row for the same tree.  `compare_backends --no-resume` kept its flag (it is documented
and has three readers) and now archives the harness cell's `run/` before re-running it.

### 7.w One driver per out dir — a second `bench run` re-runs what the first is still running

Measured 2026-08-26 (refs_v1_graphics): a redo driver (`--redo-status budget`) started while the
original driver was still working the same battery resumed a prompt the first driver had in
flight, re-ran its last round in the same workspace and rewrote `rounds/r02.json` (0.600 →
0.944 for the same sheet: judge/acceptance variance, not a new picture) and appended a second
`results.jsonl` row.  `run_battery` decides what to run from `results.jsonl`, and a prompt with no
row yet is fair game to both.  Rule: never start a second `3dcv bench run` on an out dir with a
live driver; wait for the driver to exit (exact pid, `kill -0`), then redo once with
`--redo-status infra_failed,error,budget --max-minutes 120`.  `bench run` grew `--max-minutes`
the same day so the storm budget no longer needs `ab_plan`.

### 7.x Size `--max-minutes` to the weather

`--max-minutes 60` is the right ceiling on a healthy provider.  Under a 503 storm every model
call waits through the retry budget first, an agent session spends its wall clock on 15–23
turns, and a hard prompt burns the whole hour without one judged round — measured 2026-08-26 on
`fancy_v1`: the first six pairs ended `budget` with `judge.done = 0`.  When
`codeverse.models.health.probe()` shows the generator below the 0.75 bar, launch (or redo) with
`--max-minutes 120 --wait-for-provider 60`, and redo the storm's rows rather than reading them:
`--redo-status error,infra_failed,budget` (add `build_failed` only when a harness defect, not the
model, produced the zero — check `cell.json`'s `error`).

## 8. Extending (plugin paths)

* **New language**: enum in `contracts/common.py::Language` (+ `TRACK_LANGUAGES`,
  `ENTRY_FILE`, `LANGUAGE_LABEL`), frame in `conventions.LANGUAGE_FRAME`;
  `languages/<lang>/{__init__.py, wrappers/}` (one merged module per language;
  the contract text is `prompts/<lang>/contract.md`) implementing `LanguageRuntime`; branch in `languages/base.py::get_runtime`;
  `prompts/<lang>/contract.md` + `cookbook.md` (every snippet must run —
  `tests/prompts` executes them); part→file mapping via `runtime.file_for_part`
  (blender has it; `tracks/prompting.file_for_target_factory` picks it up and maps
  every whole-object target to `[entry]` — no runtime `file_for_target` hook).
* **New spatial tool**: pydantic args + `@tool("name", Args, "…", tracks=(…),
  languages=(…), cost_hint=…)` in `spatial/tools.py`; available to tracks, MCP and prompt cards at once.
  Update `tests/spatial_tools` EXPECTED_TOOLS.
* **New rubric**: `judges/rubrics/<name>.yaml` with `pass_threshold`,
  `criteria[{id, weight, floor, title, description, anchors, kind}]`,
  `caps[{id, cap, when: gate|acceptance|console|missing_views, gate, severity, kinds}]`,
  `defects[{id, text, penalty, cap}]`.  Tracks pick rubrics in `tracks/*.py`;
  `3dcv judge` maps track → rubric in `cli/_judge.py::rubric_for`.
* **New backend**: ChatModel → `models/<provider>.py` + registry + prices;
  CodingAgent → `agents/backends.py` using `cli_common` + registry + `materialize.py`.
* **New track**: subclass `tracks/lifecycle.py::BaseTrack` (hooks: `make_pipeline`,
  `prepare`, `baseline_tasks`, `refine_tasks`, `round_files_hint`; `system_prompt`
  defaults to `language_system_prompt(ctx.language, tools=not ctx.single_shot)` and
  `refine_file_for_target` to `file_for_target_factory(ctx)` — override only for
  role-specific prompts), a `RoundPipeline`,
  plan model in `contracts/plan.py`, `.j2` prompts, branch in `tracks/__init__.py`
  (`get_track` forwards `**options` to constructors).  `tracks/graphics.py` is the
  template for a track with its own planner and no GLB.
* **New bench battery**: `bench/prompts/<name>.yaml` with `name, track, language,
  prompts[{id, tier, category, prompt, must_have, dimensions_m}]`.


## Landing source changes while a wave is running

Workers (`3dcv make`, `ab_plan.py cell`) are long-lived python processes that import
`codeverse/` **lazily**: a module already in `sys.modules` stays as it was at spawn time, a
module first touched later comes from the tree as it is *then*.  Two measured failures on
2026-08-26:

* a patch script that edited one file and aborted on the next left a two-minute window in which
  `static_object.py` imported a name `prompting.py` did not have yet — a driver died on it;
* a clean, fully tested refactor (`proc.read_json_or_none` replacing eight copies) landed on
  main at 02:47 while cells spawned at 01:56 were still generating; at eval time they lazily
  imported a rewritten module which asked their *old, cached* `proc` for the new name —
  `ImportError`, 75 minutes of generation per cell lost, recorded `status=error`.

Rules:

1. Multi-file patches validate every anchor first and write only after all pass.
2. **Do not land cross-module refactors on main under a live wave** — moved symbols, new shared
   helpers, renamed imports.  Keep them on worktree branches and cherry-pick between waves.
   Additive changes and dead-code deletions are safe; a moved name is not.
3. After landing anything, check every driver's `results.jsonl` for `ImportError` rows and redo
   them from a **fresh** driver with `--redo-status error` (the old driver's own modules are
   stale too).
4. Count harness processes with `codeverse.models.health.pool_budget()`, never `pgrep | grep`
   (it counts its own shell — measured: 3 phantoms on an idle box); kill by PID, never by
   pattern (13 unrelated runs died to one `pkill -f "3dcv make"`).
