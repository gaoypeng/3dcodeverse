# 3dcodeverse — standing context

Backend harness (python package `codeverse3d`, CLI `3dcodeverse` / short alias `3dcode`,
repo path `harness/` under the 3dcodeverse checkout) for LLM-written **raw** 3D code:
Blender bpy · CadQuery · Three.js · URDF · GLSL / OpenGL, across four tracks
(`static_object`, `articulated_object`, `scene`, `graphics`), with pluggable
backends.  **The coding agent is always a VENDOR's** — antigravity (`agy`, Google's
models) / claude-code / codex — and the harness supplies the workspace, the prompt and the
3D tools its track can use (over MCP; 16 in the registry, filtered per track — D48
review), then reads the result.  The harness's OWN api use is planning,
judging, single-shot file generation and the texture pass (Gemini/Anthropic/OpenAI).
It does not implement an agent loop: the in-process `api-agent` was deleted
2026-08-28 (owner's call — reimplementing what the vendors already ship was never
this project's job).  Plus an optional text-to-image texture pass and a
data-flywheel record per run.

Read `docs/ARCHITECTURE.md` (design, package map, authoring contracts, what a run
really does, known limits), `docs/INTERFACES.md` (binding cross-package signatures as
built), `docs/RUNBOOK.md` (how to run / resume / extend), `docs/DECISIONS.md`
(ADR-style laws + deviations), `eval/docs/EVAL.md` (evaluation protocol + judge
calibration) and `eval/docs/COMPLEXITY.md` (the objective complexity vector + the
score-vs-complexity corpus study) before changing anything.

## Laws (do not break)
1. Generated code is raw language — never an SDK/helper import; the harness owns
   wrappers/exporters (`codeverse3d/languages/wrappers/`, `languages/opengl_python/wrappers/`,
   `runtime_js/`).
2. `codeverse3d/contracts/` is data-only and shared; `conventions.py` is the only
   place that states frames/units/naming.  Import, never restate.
3. Deterministic gates/measurements run by the harness; VLM only for perception.
   Score is computed in code from rubric weights; caps/floors are explicit.
4. Typed everything; no regex-on-id control flow; a file may be long but not a god file —
   cap **2 000 lines** per file, **3 000** absolute (owner, 2026-08-28; was 1 500, and the old
   ~400-line guideline is long gone).  Merging is NOT a goal in itself — a merge must DELETE
   code, not just move it between files.
5. Cheap first: lint → build → gates → montaged views → VLM.  Budgets are hard.
6. Every round = a git commit of `src/`; every call = a `Usage`; every run = `record.json`.
7. No wrapper re-centres or grounds the object: export **as authored**, warn in the
   build, let `check_contract`/`check_connectivity` gate it.

## Supported versions (what other people can run this on)
python **3.13** (one fixed version — owner's decision 2026-08-26; no floor, no matrix) ·
node **20.6+** · Blender **5.2 LTS** only (D104, 2026-10-14; 4.2 / 5.0 compatibility dropped) · Linux x86_64.  **There is no CI** (removed 2026-08-26 by the
owner): run `ruff check codeverse3d tests` and the offline suite locally BEFORE every
push, with `set -o pipefail` so a `| tail` cannot swallow a red exit.  The version lives in
`pyproject` `requires-python` + ruff `target-version` + `setup.sh`, and
`spatial/node.py:NODE_MIN` + `runtime_js/package.json` `engines` for node, pinned together by
`tests/core/test_portability.py`.  Details: `docs/INSTALL.md` §2.1.

## Environment (this machine)
- Gemini keys: `~/.config/astra3d/gemini_keys.env` (30 keys, the owner's list of 2026-10-14: Scene1-10, Argon1-10, argon-test-1..10) → `get_settings().gemini_api_keys`
  (or `GEMINI_API_KEYS` / `GEMINI_API_KEY` env).  Settings: `~/.config/3dcodeverse/config.yaml`
  or `./3dcodeverse.yaml`, env prefix `C3D_` (the pre-D78 `CV3D_*` names are still read; the
  grammar and examples: docs/INSTALL.md §8.3).  Every `C3D_*` switch is a `Settings` field read through `get_settings()`
  where it is used — never read the environment by hand.
- Blender 5.2.2 LTS headless: `~/.local/bin/blender-5.2` (always `--factory-startup`; clear scene).  The judged-frame
  fast path is Cycles CUDA + GPU OIDN + persistent data (D104; measured 0.21 s/frame at 1024x576 after the first);
  OptiX is unavailable under WSL2 and probing it crashes Blender — never enumerate it.
- Node 24 (floor 20.6) + `runtime_js/node_modules` (three@0.182, puppeteer; chrome cached).  Headless
  Chrome WebGL uses the GPU on WSL2 with `--use-angle=gl-egl` + Mesa d3d12 env (see
  `runtime_js/gpu_launch.cjs`; `C3D_RENDER_GPU=on|off|auto`); SwiftShader fallback.
  Bare ESM `import 'three'` needs `--import runtime_js/lib/resolve_three.mjs`
  (`spatial.node.run_node(three_hook=True)`) — `NODE_PATH` alone does not work;
  that hook is `module.registerHooks` on node ≥ 22.15, `module.register` below.
  The `graphics` track renders with moderngl (d3d12 GPU context, llvmpipe fallback).
- CLIs: `agy` 1.3.4 (Antigravity; gemini-cli was deleted in D103, 2026-10-13) — runs ONLY in
  Gemini-API mode (Gemini models only, keys required; no subscription sign-in): one key from the
  shared pool, a per-session HOME carrying `modelProvider: gemini` + the 3dcode MCP server, priced.  `codex` 0.147+ (MCP needs
  `default_tools_approval_mode="approve"`) and `claude` 2.1 run on local subscriptions: test
  lightly.  No Anthropic/OpenAI API keys here.
- DEFAULT generator: `agy:gemini-4-argon` (D103, 2026-10-13; `gemini-cli:gemini-3.7-flash` before); planner and
  captioner default to `gemini:gemini-4-argon` too.
  `gemini-3.7-flash` stays the cheap API model (single-shot / economy, flash judge — lenient; needs
  the defect checklist for range).  Judge default `gemini-3.1-pro-preview` (`Settings.default_judge`; tracks use
  n_samples=1); flash + `--n 3` is the cheap fallback, pro for calibration/eval.  The argon loop judges with
  `gemini:gemini-4-argon` at `judge.thinking=medium` (argon does not think at 'low'; D105) on rubric `scene_v2`.
- Live runs under `runs/` (`e2e_*`): read-only reference material; never modify.
- Every model call and vendor session is priced into the run's `telemetry/cost.jsonl` — the ONLY record of
  money: `record.total_usage` is its sum at list price (D84).  A run's minutes are its steps' time minus
  the time lost to provider errors (`RunRecord.minutes`).  `docs/COST.md` Part II has the cost controls.

## Commands
```
pip install -e harness   # once, from the 3dcodeverse checkout (entry points: 3dcodeverse, 3dcode)
cd harness
3dcodeverse doctor [--live] [--no-gpu] [--json]
3dcodeverse make "a mid-century wooden dining chair" --track static_object --language blender
3dcodeverse make "..." --profile economy|balanced|quality   # one dial: models, judge n, rounds, candidates, texture, ceilings (--profile == C3D_PROFILE)
3dcodeverse make "..." --track static_object --language threejs --generator agy:gemini-4-argon
3dcodeverse make "..." --track articulated_object --language urdf_blender
3dcodeverse make "..." --track scene --language scene_threejs --rounds 2 --max-minutes 60
3dcodeverse make "..." --track scene --language scene_blender --rounds 2 --max-minutes 90   # bpy scene on Blender 5.2.2 (D105): harness-owned src/lib, Cycles judged frames
# Every track states REQUIREMENTS, never templates (D106 scene, D107 the rest, 2026-10-15): no language ships a cookbook, no inlined
# recipes, placeholder skeletons; a prompt / skill keeps only interface, sandbox rules and measured thresholds, each contract.md ONE
# executable interface example (tests/prompts/test_snippets_run.py), the craft as short optional practices in the skills (fences ≤ ~12 lines).
3dcodeverse make "..." --variant argon      # per-model prompt/skill overlay: prompts/variants/<name>/ + skills/variants/<name>/ (D105; C3D_VARIANT, C3D_VARIANTS='{"agy:gemini-4-argon": "argon"}')
3dcodeverse make "..." --track scene --language scene_blender --variant argon_raw   # RAW mode (D113): no src/lib ships, argon writes every line of bpy ([knobs] scene_lib = false; C3D_SCENE_LIB)
3dcodeverse make "neon rain on a window" --track graphics --language glsl_shader
3dcodeverse make "..." --image ref.png --candidates 3 --rounds 2 --dim height=0.45 --must "three legs" --texture --no-run
3dcodeverse make "..." --no-pick            # a run = baseline + --rounds refine rounds (fixed); make then picks a round → deliverable/ unless --no-pick
3dcode pick <slug> [--by score|pairwise] [--round N] [--texture] [--judge MODEL]   # hand over a round (codeverse3d/addons/select.py)
3dcode resume <slug> · 3dcode show <slug> [--section status] (`status` = its STATUS section) · 3dcode render <slug> [--mode wire] · 3dcode judge <slug> [--model ... --n 3 --thinking medium]
3dcode texture pass <slug> [--no-judge] · 3dcode texture scene-pack <slug> · 3dcode texture show <slug>
3dcode tools list · 3dcode tools measure --workspace runs/<slug> · 3dcode mcp --workspace runs/<slug>
3dcode cost <slug> · 3dcode cost --runs-dir ../eval/bench/out/<battery> · 3dcode cost cache <slug>
3dcode cost prices [--stale] · 3dcode cost profiles · 3dcode cost estimate gemini:gemini-3.1-pro-preview --in 12000
3dcode flywheel export runs/ dataset/ [--pack --drop-duplicates --captions-dir d/] · 3dcode flywheel pairs runs/ pairs.jsonl
3dcode flywheel caption <slug> [--out dir]   # the gallery is `3dcode gallery build --embed`
3dcode gallery serve [ROOTS...] [--port 8765] [--reload] · 3dcode gallery build --out gallery.html [--embed]
(cd ../eval && python -m bench.run_bench bench/prompts/static_objects_v1.yaml --generator ... --judge gemini:gemini-3.1-pro-preview)   # report: python -m bench.report bench/out/<battery>
python -m codeverse3d.addons.calibration runs/<slug>... --model gemini:gemini-3.1-pro-preview --n 3 --out out/
(cd ../eval && python -m bench.loop init --name argon_scene --out bench/out/loop_argon --variant argon --languages scene_threejs,scene_blender --judge gemini:gemini-4-argon --judge-thinking medium --parallel 15 && python -m bench.loop generate --iter 0 && python -m bench.loop run --iter 0 && python -m bench.loop diagnose --iter 0)   # the argon loop (eval/docs/EVAL.md §10): then compare --iter N --against M · judge-calib --iter N · accept|rollback --iter N · status
python ../eval/bench/complexity_report.py ../eval/bench/out --recursive   # score-vs-complexity + $/complexity point (eval/docs/COMPLEXITY.md)
python ../eval/bench/compare_backends.py --prompts ../eval/bench/prompts/compare_v1.yaml --arms harness:agy:gemini-3.6-flash,oneshot:claude-code --judge gemini:gemini-3.1-pro-preview --out ../eval/bench/out/compare_v1
python -m tests.prompts.manifest [--bless | --dump DIR]   # what every model is shown, pinned (RUNBOOK §8): bless only a change a model is MEANT to see
python -m pytest tests -q -m "not live"            # the offline suite (real Blender + headless Chrome + CadQuery)
python -m pytest tests -q -m "not live and not blender and not node"   # pure python   (the bench tests: `cd ../eval && python -m pytest`)
# (how many: `--collect-only`; a count written here drifts every commit)
# both run PARALLEL by default (pytest-xdist, -n auto --dist worksteal, in pyproject addopts).
# A nested pytest inside a test MUST pass -n0 or it forks another full set of workers.
```
