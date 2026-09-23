# Runbook — operating the harness

Everything here was exercised on this machine (WSL2, RTX 5090, Blender 5.0.1,
node 24, gemini-cli 0.53, claude 2.1, codex 0.147+, agy 1.1) on 2026-08-23.  Paths
are relative to `/home/yipeng/3dcodeverse/harness` unless absolute.  The CLI is
`3dcodeverse` with short alias `3dcode` (used below).

## 1. Install and check

```bash
pip install -e /home/yipeng/3dcodeverse/harness      # once; entry points 3dcodeverse and 3dcode
cd /home/yipeng/3dcodeverse/harness/runtime_js && npm install   # three@0.182, puppeteer (chrome cached)
3dcode doctor            # python deps (incl. python-fcl, moderngl), blender, node/three/puppeteer, chrome WebGL, keys, CLIs, git, mcp
3dcode doctor --live     # + one tiny Gemini call ("pong", ~$0.00001)
python -m pytest tests -q                              # offline suite; live tests are opt-in (blender/node/GL run when the binaries exist)
python -m pytest tests -q -m "not live and not blender and not node"   # pure-python subset
python -m pytest tests -q -m live                      # OPT-IN: real API calls
```

### Keys and settings
Gemini keys, the other providers' keys, the settings files, every `C3D_*` override, how
Blender is found and how the GPU path is chosen are `docs/INSTALL.md` §8, §6 and §7 (their
one home); each setting's default and description is its `Settings` field in
`codeverse3d/config.py`.

## 2. Running each track

```bash
# static object — Blender (default language, multi-file src/model.py + src/parts/*.py)
3dcode make "a mid-century wooden dining chair" --track static_object --language blender
3dcode make "a brass desk lamp" --track static_object --language cadquery
3dcode make "a classic park bench" --track static_object --language threejs --generator gemini-cli:gemini-3.7-flash
# articulated object — bpy links + URDF
3dcode make "a bedside cabinet with one hinged door and one drawer" --track articulated_object --language urdf_blender
# scene — multi-file three.js + GLSL (+ optional bpy GLB assets chosen by the planner)
3dcode make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs --rounds 2 --max-minutes 60
# graphics — animated shader / raw OpenGL program
3dcode make "neon cyberpunk rain on a window with bokeh city lights" --track graphics --language glsl_shader
3dcode make "instanced pastel cubes with bloom" --track graphics --language opengl_python
```
Useful flags (`3dcode make --help`): `--generator`, `--planner`, `--judge`, `--captioner`
(backend ids, §3), `--image <png>` (repeatable; reference images → `ReferenceJudge` +
silhouette gate + IoU refine tasks), `--rounds N` (refine rounds after the baseline — a
run does exactly N, each built on the one before it, unless the clock or a hard failure
stops it; there is no pass / plateau / regression stop since 2026-09-22),
`--candidates N` (best-of-N baseline: N parallel candidates in `<ws>/_cand/`,
quick-judged, the highest quick score kept (fewer gate errors on a tie); multiplies baseline
cost ≈ N; default from `settings.default_candidates`; object and graphics tracks only —
a scene writes its baseline in its stages, so `--candidates N>1` is refused there and a
profile's best-of-2 runs one), `--texture` (texture the PICKED round
after the run; see §6), `--no-pick` (package nothing: no `deliverable/`, no `selection.json` —
`3dcode pick` later), `--max-minutes`, `--dim height=0.45`, `--must`,
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
| `gemini-cli:gemini-3.7-flash` (default) | `gemini -m … --approval-mode yolo --skip-trust --output-format json`, prompt on stdin; every spatial tool over MCP | cheapest agentic path; see gotchas below |
| `single-shot:gemini:gemini-3.7-flash` | one structured-output call → multi-file envelope, no tools | fastest/cheapest; baseline for "raw model" deltas |
| `claude-code:<model>` | `claude -p --dangerously-skip-permissions --setting-sources project --settings '{"skillOverrides":…}' --mcp-config trajectories/<label>_rNN/mcp.json --strict-mcp-config …`, prompt on stdin, `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` | local subscription — test lightly; no user setting source, so `~/.claude/settings.json` (effortLevel, hooks, env) does not reach a session |
| `codex:<model>[@<effort>]` | `codex exec --json -C ws --sandbox workspace-write -c model_reasoning_effort=high -c skills.bundled.enabled=false -c mcp_servers.3dcode.… -`, prompt on stdin | subscription; MCP tools need `default_tools_approval_mode="approve"` (harness passes it); reasoning effort is always stated (`Settings.agents.codex_reasoning_effort`, default `high`; `codex:gpt-5.6-sol@medium` per id, `""` to defer to `~/.codex/config.toml`) |
| `agy:<model>` | `agy --add-dir ws --log-file trajectories/<label>_rNN/agy.log …`, prompt on stdin (no `--print`: `--print -` sends "-") | no per-workspace MCP: tools via `3dcode tools <name> --json … --workspace .`; no served-model or cost reporting; truncates a prompt past ~175 kB itself |
| `gemini:* / anthropic:* / openai:*` | ChatModel for planner / judge / captioner / single-shot | Anthropic/OpenAI untested live here |

### gemini-cli gotchas (handled by `agents/backends.py`; do not undo)
* System settings file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH`: api-key auth,
  `experimental.dynamicModelConfiguration=true` (else unknown models are silently
  substituted → checked, `exit_reason=model_substituted`), `security.folderTrust.enabled=false`
  (else workspace MCP servers are silently ignored even with `--skip-trust`).
* Workspace `.gemini/settings.json` sets `context.fileFiltering.respectGitIgnore=false`
  so the fine-grained `.geminiignore` (not the git ignore) decides what the agent can
  read: build/census/measurement JSON stay readable, renders/judge output and the whole
  `trajectories/` stay hidden (the prompt comes on stdin; there is no prompt file).
* The system settings also disable gemini-cli's two built-in skills (`skills.disabled`),
  so a session lists only the routed bundles.
* Usage when the CLI printed no envelope (watchdog kill; a give-up after its own 503
  retries, which writes an error object to STDERR and exits 247): the attempt's chat
  record under `~/.gemini/tmp/<project>/chats/` — `result.json` `usage_from` says which.
* One pool key injected as `GEMINI_API_KEY`; other credential env stripped; retries
  prefer a different key (never raise KeyPoolExhausted out of a session).
* Cost accounting: `tokens.prompt` (total, incl. cached) is the input count; each
  served model priced at its own rate.
* Cookbook copied to `ws/.3dcode/cookbook.md` (gemini-cli cannot read outside the ws).
* MCP server argv is `[sys.executable, -m, codeverse3d.spatial.mcp_server, --workspace, ws]`;
  server name `3dcode` → tools appear as `mcp_3dcode_<name>` (gemini) / `mcp__3dcode__<name>`
  (claude).  `3dcode mcp --workspace runs/<slug>` execs the same server;
  `python -m codeverse3d.spatial.mcp_server --workspace ws --list` prints the tools.

## 4. Where outputs land

Everything lands in `runs/<slug>/` — code in `src/` (git, one commit per round), each round's
build in `artifacts/rNN/`, and after a pick `deliverable/` + `selection.json`; the full tree is
`docs/RUN_LAYOUT.md`.  `3dcode status <slug>` prints the stop reason, baseline → picked
score, the rounds table (the picked round starred, each round's own judge verdict), cost and
the last events.

### Looking at results locally

```bash
3dcode gallery serve                       # ./runs + every eval/bench/out/*/runs → http://127.0.0.1:8765/
3dcode gallery serve ../eval/bench/out/static_v2_flash/runs --port 9000 --reload --no-open
3dcode gallery build --out gallery.html [--embed]      # one self-contained file (--embed inlines the sheets)
```
`serve` indexes the run roots (records only — ~85 runs in ~0.15 s) and serves the run
**directories** too, so every link works: contact sheet, full-size renders, `record.json`,
`plan.json`/`spec.json`, the `src/` tree (browsable, line-numbered, `raw` = text/plain),
`object.glb` (orbit viewer on the vendored three.js — no network), articulation sheets,
`preview.gif`, frames, textures.  The page has a live summary strip (n = judged · unjudged ·
error, mean/median score, total $, wall clock — **recomputed per filter**; no pass rate: a run
is not passed or failed),
filters (track / language / tier / backend / verdict / battery + text search over
prompt+slug) and sort (score / cost / time / name) that never reload, a card ⇄ table
toggle, light/dark, and a detail page per run (`/run/<battery>/<slug>`) with every round,
the picked round's judge verdict + issues + improvement plan, the measurement table, the
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
3dcode resume <slug> [--candidates N] [--no-pick]
                                        # continues from run_state + stages/*.json (input-hash cached) and from the
                                        # LAST round (a run recorded before 2026-09-22 that restored its best round
                                        # is put back on its last).  A max_rounds run resumes only with a raised
                                        # --rounds, a budget run with any raised cap; an agent_quota or failed run
                                        # resumes as is; the other stops are finished.  The budget
                                        # SNAPSHOT is restored: the active minutes already spent still count, so a
                                        # raised --max-minutes grants only the difference (downtime never counts);
                                        # the money is the ledger's, and the step log (the run's minutes) carries on.
                                        # Once rounds exist a recorded stage never runs again: one whose key drifted (a
                                        # code change to what it hashes) serves its recorded result (`stage.frozen`); only
                                        # `resume --force` regenerates a stage under them: it ALWAYS archives the round
                                        # journal + record.json to rounds/pre_force/ (spec changed or not) and the run
                                        # starts again at round 0, no stage frozen
3dcode pick <slug> [--by score|pairwise] [--round N] [--texture] [--judge MODEL]
                                        # hand over a round: deliverable/ + selection.json (addons/select).  score =
                                        # highest effective score, ties → fewer gate errors → the earlier round;
                                        # pairwise = the pairwise judge between the top two when within 0.03 (one
                                        # paid verdict per pair, cached); --round N = that round; --texture = the
                                        # texture pass on it first.  `make`/`resume` run `pick --by score` for you
3dcode render <slug> [--round N] [--mode shaded|wire|normals|clay|silhouette] [--out dir]
3dcode judge <slug> [--round N] [--rubric static_object_v1] [--model gemini:gemini-3.1-pro-preview] [--n 3]
                                        # re-judges a round's recorded renders → artifacts/judge/rNN_cli.json
3dcode texture pass <slug> [--no-judge] [--model …] [--judge-model …] [--image-model …] [--size 1024]
3dcode texture scene-pack <slug> [--n 10] · 3dcode texture show <slug>
3dcode tools list [--cards] · 3dcode tools measure --workspace runs/<slug> · 3dcode tools gl_frames --workspace … --json '{"times":[0,1,2.5]}'
                                        # panel says ok | FAIL (the verdict) | error (the tool could not run);
                                        # exit 1 on either non-ok state — `scene_probe` included since 2026-09-02
                                        # (COST.md §30), where a failing scene gate used to exit 0
3dcode flywheel export runs/ dataset/ [--min-score 0.7] [--only-passed] [--pack] [--include-unbuilt]
                                     [--captions-dir caps/] [--drop-duplicates]
3dcode flywheel pairs runs/ pairs.jsonl [--min-delta 0.05]
3dcode flywheel refine runs/ refine.jsonl [--with-code]
                                     # one row per round the harness asked to change; the row
                                     # schema is codeverse3d/addons/dataset/refine.RefineTransition and
                                     # INTERFACES has the call signatures.  Training formats live
                                     # in toolkits/llamafactory/, not here.
3dcode flywheel caption <slug> [--model …] [--out caps/]      # --out = side-car mode, run untouched
3dcode flywheel index runs/ runs_index.sqlite
python -m codeverse3d.addons.calibration runs/<slug> [runs/<slug2> …] --model gemini:gemini-3.1-pro-preview --n 3 --out out/
                                        # re-judges recorded rounds; writes calibration_<model>.md/.json (never touches runs/)
```
**Do not run the offline suite while a battery is running.**  The suite runs thousands of tests
on 24 cores under xdist; a battery holds Blender, a browser pool and several agent sessions.
Twice on 2026-09-04/05 that produced 11 and 21 spurious failures that vanished on a clean
re-run (`tests/scene_runtime/lib/*` first, since those hold the renderer longest).  A red
suite during a battery is **not evidence until it reproduces on a quiet machine** — re-run
it there before pushing, and if the same tests are red again, it is real.  The mechanism is
the one the next paragraph describes: `tests/scene_runtime/lib/*` reach the battery's
browser daemon through a shared `C3D_CACHE_DIR`, so giving the suite its own cache dir may
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

**Two arms at once still want separate `C3D_CACHE_DIR`, though the browser no longer
depends on it.**  Until 2026-09-05 the daemon advertised its endpoint at
`CACHE_DIR/browser_<backend>.json`, a newer daemon superseded an older one, and two
worktrees sharing a cache ended up on ONE browser: the arm that did not launch it
rendered through a server rooted in the other tree, its GLB was outside that root, and
`render_glb` returned "produced no result" — on one side only, looking like random
flakiness (2026-09-04).  The endpoint, its lock and its failure file now carry a digest of
the `runtime_js` that spawned the daemon, so two trees keep separate browsers inside one
cache dir and neither can retire the other's.  Give each arm its own cache dir anyway: the
render cache separates with it, which an A/B wants.

Also halve each arm's `--parallel`: two arms at 3 workers is six concurrent renders, and a
round with no renders skips the judge, so the loop stops at `judge_unavailable` and the
cell is finished with no score (`run_bench` will not re-run it: the row exists).  Redo the
scoreless cells afterwards — delete the row from `results.jsonl` and the run directory,
then `python -m bench.run_bench <battery> --id <prompt>`.

A refine session that dies on the VENDOR's usage limit ("You've hit your usage limit … try again at
Sep 14th", `RESOURCE_EXHAUSTED`, `insufficient_quota`) stops the run at `agent_quota`: every round
so far is kept and picked from as usual, the record says the agent's budget ended, not the code's
improvement (cmp8, 2026-09-09, filed three such runs as `plateau` before this), and `3dcode resume`
continues it once the limit resets.

**A cell can also lose its renders to the box rather than to itself.**  When the machine
runs out of memory Chrome reaps the render tab and the driver reports
`Attempted to use detached Frame '<id>'`: the scene built, every gate ran, and the judge
was skipped for want of pixels — three of six cells of `scenes_v1` on 2026-09-05, $6.75 of
generation already paid for.  The scene drivers now retry once on a browser of their own
(`spatial/render_scene.run_scene_script`), and `eval/bench/scene_stats.py` reports such rounds
in a `lost to the box` column instead of counting them as gate failures.  A battery whose
report shows that column non-zero has to be re-run for those cells before it is read as a
statement about the generator.

**Anything launched with `3dcode` from a worktree runs the MAIN checkout's code.**  `3dcode`
is a console script, so `sys.path[0]` is the venv's `bin`, never the cwd, and `import
codeverse3d` finds the editable install.  `--out` IS relative to the cwd, so the OUTPUT lands
in the worktree while the CODE that produced it is the main tree's — an arm that looks like it
is testing your branch and is testing `main` (measured 2026-09-05: the first `scenes_v1`
battery, launched with `3dcode bench run` from `local/worktrees/integrate`, ran entirely on the
main checkout).  Batteries no longer have this problem: `python -m bench.run_bench` (the
launcher since 2026-09-22) bootstraps `sys.path` to its own tree's `harness/` like every bench
script (`eval/tests/test_worktree_import.py`).  For `3dcode make` from a worktree, export
`PYTHONPATH=<worktree>/harness` before the command, and have an arm script REFUSE TO RUN when
`codeverse3d.__file__` and `runtime_js_dir()` are not the tree you meant —
`local/scripts/run_scene_fixed.sh` is the pattern, and it also asserts that the specific fixes
the arm exists to measure are present.

**A new worktree needs `runtime_js/node_modules` before it can run a battery.**  Without it
`render_glb` dies on every round, the judge is skipped for want of renders, and the cells
came back `status=plateau` (today: `judge_unavailable`) with `score=None` — an arm that reads as healthy and measures
nothing (2026-09-04, the mimic-off arm).  `npm ci` in `runtime_js/`, or COPY the
directory from a worktree that has it (the `package.json` is the same file).  The row now (a symlink gave two 404s on every render of one worktree on 2026-09-07 — `serve.cjs` refuses a real path outside its root — while another probe served through one; copy and be sure)
says `no verdict in any of N round(s)` when this happens.

A run that crashed outside its own handling leaves `record.json` with `status=failed`;
`3dcode resume` retries from the last completed stage/round (cached plan/skeleton/scene
stages are reused — this also recovers from Gemini 503 storms).  Ctrl-C is safe.

## 6. Texture pass

`3dcode pick <slug> --texture` (or `--texture` on `make`, which picks after the run) textures the
PICKED round's own `artifacts/rNN/object.glb` — once per round: a pass that already started from
those bytes is re-used, never re-bought; `3dcode texture pass <slug>` textures the same picked
round's GLB.  Either way: one VLM material plan →
tileable texture images (gemini-3.1-flash-image, ~$0.07/tile, cached by prompt) →
world-metre UVs → `artifacts/object_textured.glb` → seam gate + before/after judge
gate (ships only when the score does not drop and the materials criterion improves).
`record.extra["texturing"]` holds shipped/delta/cost; `3dcode texture show <slug>`
prints it.  Scenes: `3dcode texture scene-pack <slug>` writes 6–12 named tiles +
manifest under `public/textures/` for zone prompts.  There is no texture tool in an
agent session (the `texture_pass` / `texture_preview` tools were deleted 2026-09-22:
one call in 616 recorded sessions).

## 7. Benchmarks

```bash
cd ../eval   # the battery launcher is the evaluation's (`3dcode bench` until 2026-09-22)
python -m bench.run_bench bench/prompts/static_objects_v1.yaml --generator single-shot:gemini:gemini-3.7-flash \
    --judge gemini:gemini-3.1-pro-preview --rounds 2 --parallel 4 [--tier easy] [--id furn_easy_stool] [--limit 6] [--out bench/out/x]
python -m bench.report bench/out/static_objects_v1      # report.md + self-contained report.html (gallery)
cd ../harness
python ../eval/bench/compare_backends.py --prompts ../eval/bench/prompts/compare_v1.yaml \
    --arms harness:gemini-cli:gemini-3.7-flash,oneshot:claude-code --judge gemini:gemini-3.1-pro-preview --out ../eval/bench/out/compare_v1
```
Results stream to `results.jsonl` (resumable).  The batteries, the protocol and judge
calibration: `eval/docs/EVAL.md` (§2 lists the batteries).

`compare_backends` **preflights every model it needs** (one ~20 s probe each) and
refuses to start when one is not serving — a dead provider does not fail fast on its
own, it lets each cell burn its full retry budget first.  `--wait-for-provider 60`
parks until it recovers instead; `--no-preflight` skips the check.  Cells that a
provider outage kills anyway are recorded `infra_failed`, excluded from every rate,
and re-run with `--redo-status infra_failed` (see `eval/docs/EVAL.md` §7).

### 7.v Templates are read at render time — a new required variable breaks live workers like a moved name

Measured 2026-08-26 13:45: the fewer-turns landing added `{{ turn_discipline }}` to four static
templates; the four h2h drivers started 50 minutes earlier still ran the old python (no such
context key) but rendered the NEW template from disk → `UndefinedError: 'turn_discipline' is
undefined`, four prompts recorded `error` at 0 min.  Rule: every new template variable is
guarded `{% if name is defined and name %}` for at least one wave, and the "Landing source
changes while a wave is running" rule covers `codeverse3d/prompts/**` as well as moved names.

### 7.u `run_bench --redo-status` starts the redo fresh (since c828637)

Before it, a redo resumed the old workspace — old `spec.json` (the old `max_minutes`) and the
old clock — so a `budget` row redone with `--max-minutes 120` was over budget before its first
round (clock_q4, lighthouse_1: `budget`, 0 rounds, "60.3 / 76.7 min elapsed").  Now the old
tree is archived as `runs/<id>.attempt<N>` and the prompt runs from scratch with the new
options, the way `ab_plan` has done since the skills wave.

It is also the ONLY way: `run_bench --no-resume` was deleted 2026-08-30.  It dropped the
recorded rows and then resumed the workspace anyway — `resume = ws.exists()` never read the
flag — so a "fresh" rerun carried the old spec, rounds, spend and clock and appended a second
results row for the same tree.  `compare_backends --no-resume` kept its flag (it is documented
and has three readers) and now archives the harness cell's `run/` before re-running it.

### 7.w One driver per out dir — a second `run_bench` re-runs what the first is still running

Measured 2026-08-26 (refs_v1_graphics): a redo driver (`--redo-status budget`) started while the
original driver was still working the same battery resumed a prompt the first driver had in
flight, re-ran its last round in the same workspace and rewrote `rounds/r02.json` (0.600 →
0.944 for the same sheet: judge/acceptance variance, not a new picture) and appended a second
`results.jsonl` row.  `run_battery` decides what to run from `results.jsonl`, and a prompt with no
row yet is fair game to both.  Rule: never start a second `python -m bench.run_bench` on an out dir
with a live driver; wait for the driver to exit (exact pid, `kill -0`), then redo once with
`--redo-status infra_failed,error,budget --max-minutes 120`.  `run_bench` grew `--max-minutes`
the same day so the storm budget no longer needs `ab_plan`.

### 7.x Size `--max-minutes` to the weather

`--max-minutes 60` is the right ceiling on a healthy provider.  Under a 503 storm every model
call waits through the retry budget first, an agent session spends its wall clock on 15–23
turns, and a hard prompt burns the whole hour without one judged round — measured 2026-08-26 on
`fancy_v1`: the first six pairs ended `budget` with `judge.done = 0`.  When
`codeverse3d.models.health.probe()` shows the generator below the 0.75 bar, launch (or redo) with
`--max-minutes 120 --wait-for-provider 60`, and redo the storm's rows rather than reading them:
`--redo-status error,infra_failed,budget` (add `build_failed` only when a harness defect, not the
model, produced the zero — check `cell.json`'s `error`).  The rows' `minutes` stay comparable across
weather: they leave the provider's errors out (docs/COST.md §31); the ceiling does not.

## 8. Extending (plugin paths)

* **New language**: enum in `contracts/common.py::Language` (+ `TRACK_LANGUAGES`,
  `ENTRY_FILE`, `LANGUAGE_LABEL`), frame in `conventions.LANGUAGE_FRAME`;
  `languages/<lang>/__init__.py` (one merged module per language) implementing
  `LanguageRuntime` — its file layout included: subclass `languages/base.RuntimeLayout`
  (entry + `extra_files`, and `part_file` for a one-file-per-part language) or answer
  `expected_files(plan)` / `files_for(plan, target)` yourself, as the scene runtime does; the
  tracks never restate it.  A python-executed build wrapper goes beside the others in
  `languages/wrappers/` (import `_wrapper_common`; run it with `_common.run_wrapper_build`, or
  read its report with `_common.compose_build_result`, which publishes the final `build.json`); branch in
  `languages/base.py::get_runtime`; `prompts/<lang>/{system,contract,cookbook}.md` (found by
  `prompts/catalog.language_prompt`; every cookbook snippet must run — `tests/prompts`
  executes them).
* **Changing what a model is shown** (a prompt file or template variable, a cookbook, a skill,
  a rubric's text, a tool description, anything a session or a judge reads):
  `tests/prompts/test_manifest.py` pins every payload of offline scenarios across the four tracks
  and seven languages — agent sessions, chat calls (planner, single-shot), each judge's input and
  the request the real `VlmJudge` builds from it, the materialised workspace files, the routed
  skills, the MCP tool lists — and every stage's cache key.  A refactor leaves it byte-identical;
  a change a model is meant to see re-blesses it in the same commit, saying why:
  `python -m tests.prompts.manifest --bless` (`--dump DIR` writes every payload, to `diff -r` two
  trees).  What it cannot drive is listed in `tests/prompts/manifest.py`.  A new prompt file must be
  loaded by the package (`tests/prompts/test_files.py`) or sit in its `UNREACHED` with the reason; a
  new language or track adds its scenario to `SCENARIOS`.
* **New spatial tool**: pydantic args + `@tool("name", Args, "…", tracks=(…),
  languages=(…), cost_hint=…)` in `spatial/tools.py`; available to tracks, MCP and prompt cards at once.
  Update `tests/spatial_tools` EXPECTED_TOOLS.
* **New rubric**: `judges/rubrics/<name>.yaml` with `pass_threshold`,
  `criteria[{id, weight, floor, title, description, anchors, kind}]`,
  `caps[{id, cap, when: gate|acceptance|console|missing_views, gate, severity, kinds}]`,
  `defects[{id, text, penalty, cap}]`.  Tracks pick rubrics in `tracks/*.py`;
  `3dcode judge` maps track → rubric in `cli/_judge.py::rubric_for`.
* **New backend**: ChatModel → `models/<provider>.py` + registry + prices;
  CodingAgent → `agents/backends.py` using `cli_common` + registry + `materialize.py`.
* **New track**: subclass `tracks/lifecycle.py::BaseTrack` (its pre-round graph `stages`, a tuple of
  `StageNode`s — the default is skeleton → materialize, and a new track adds its line to
  `docs/ARCHITECTURE.md` §7.1; hooks: `make_pipeline`, `baseline_tasks`, `refine_tasks`; `system_prompt`
  defaults to `language_system_prompt(ctx.language, tools=not ctx.single_shot)` and
  `refine_file_for_target` to the runtime's `files_for` — override only for
  role-specific prompts), a `RoundPipeline`,
  plan model in `contracts/plan.py`, `.j2` prompts, the planner's per-track rows in
  `tracks/planner.py` (template, example, temperature, acceptance), branch in
  `tracks/__init__.py` (`get_track` forwards `**options` to constructors).
  `tracks/graphics.py` is the template for a track with no GLB.
* **New bench battery**: `eval/bench/prompts/<name>.yaml` with `name, track, language,
  prompts[{id, tier, category, prompt, must_have, dimensions_m}]`.


## Landing source changes while a wave is running

Workers (`3dcode make`, `ab_plan.py cell`) are long-lived python processes that import
`codeverse3d/` **lazily**: a module already in `sys.modules` stays as it was at spawn time, a
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
4. You do not need to count harness processes to protect the provider: the in-flight slots are
   machine-wide (`docs/COST.md` §23; `3dcode doctor` prints how many are busy).  If you list
   processes anyway, never with `pgrep | grep` (it counts its own shell — measured: 3 phantoms
   on an idle box); kill by PID, never by pattern (13 unrelated runs died to one
   `pkill -f "3dcode make"`).
