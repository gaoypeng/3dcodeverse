# 3dcodeverse — standing context

Backend harness (python package `codeverse`, CLI `3dcodeverse` / short alias `3dcv`,
repo path `/home/yipeng/3dcodeverse/harness`) for LLM-written **raw** 3D code:
Blender bpy · CadQuery · Three.js · URDF · GLSL / OpenGL, across four tracks
(`static_object`, `articulated_object`, `scene`, `graphics`), with pluggable
backends.  **The coding agent is always a VENDOR's** — gemini-cli / claude-code /
codex / antigravity — and the harness supplies the workspace, the prompt and its 20
3D tools (over MCP), then reads the result.  The harness's OWN api use is planning,
judging, single-shot file generation and the texture pass (Gemini/Anthropic/OpenAI).
It does not implement an agent loop: the in-process `api-agent` was deleted
2026-08-28 (owner's call — reimplementing what the vendors already ship was never
this project's job).  Plus an optional text-to-image texture pass and a
data-flywheel record per run.

Read `docs/ARCHITECTURE.md` (design, package map, authoring contracts, what a run
really does, known limits), `docs/INTERFACES.md` (binding cross-package signatures as
built), `docs/RUNBOOK.md` (how to run / resume / extend), `docs/DECISIONS.md`
(ADR-style laws + deviations), `docs/EVAL.md` (evaluation protocol + judge
calibration) and `docs/COMPLEXITY.md` (the objective complexity vector + the
score-vs-complexity corpus study) before changing anything.

## Laws (do not break)
1. Generated code is raw language — never an SDK/helper import; the harness owns
   wrappers/exporters (`codeverse/languages/*/wrappers`, `runtime_js/`).
2. `codeverse/contracts/` is data-only and shared; `conventions.py` is the only
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
node **20.6+** · Blender 4.2+ · Linux x86_64.  **There is no CI** (removed 2026-08-26 by the
owner): run `ruff check codeverse bench tests` and the offline suite locally BEFORE every
push, with `set -o pipefail` so a `| tail` cannot swallow a red exit.  The version lives in
`pyproject` `requires-python` + ruff `target-version` + `scripts/setup.sh`, and
`spatial/node.py:NODE_MIN` + `runtime_js/package.json` `engines` for node, pinned together by
`tests/core/test_portability.py`.  Details: `docs/INSTALL.md` §2.1.

## Environment (this machine)
- Gemini keys: `~/.config/astra3d/gemini_keys.env` (22 keys) → `get_settings().gemini_api_keys`
  (or `GEMINI_API_KEYS` / `GEMINI_API_KEY` env).  Settings: `~/.config/codeverse/config.yaml`
  or `./codeverse.yaml`, env prefix `CV3D_` (`CV3D_RENDER__GPU=off`, `CV3D_RUNS_DIR=…`,
  `CV3D_DEFAULT_CANDIDATES=2`).
- Blender 5.0.1 headless: `~/.local/bin/blender-5.0` (always `--factory-startup`; clear scene).
- Node 24 (floor 20.6) + `runtime_js/node_modules` (three@0.182, puppeteer; chrome cached).  Headless
  Chrome WebGL uses the GPU on WSL2 with `--use-angle=gl-egl` + Mesa d3d12 env (see
  `runtime_js/gpu_launch.cjs`; `CV3D_RENDER_GPU=on|off|auto`); SwiftShader fallback.
  Bare ESM `import 'three'` needs `--import runtime_js/lib/resolve_three.mjs`
  (`spatial.node.run_node(three_hook=True)`) — `NODE_PATH` alone does not work;
  that hook is `module.registerHooks` on node ≥ 22.15, `module.register` below.
  The `graphics` track renders with moderngl (d3d12 GPU context, llvmpipe fallback).
- CLIs: `gemini` 0.53 (api-key auth + `dynamicModelConfiguration` + `folderTrust.enabled=false`
  via the system settings file the harness writes — else it silently substitutes models /
  drops workspace MCP servers), `codex` 0.147+ (MCP needs
  `default_tools_approval_mode="approve"`), `claude` 2.1, `agy` 1.1 — the last three run
  on local subscriptions: test lightly.  No Anthropic/OpenAI API keys here.
- Main test model: `gemini-3.7-flash` — and since 2026-08-28 it is also the DEFAULT
  generator (`gemini-cli:gemini-3.7-flash`), verified live through the harness on both
  vendor paths: gemini-cli completes and is not silently substituted, and `agy` 1.1.22
  lists and serves `gemini-3.7-flash-{high,medium,low}`.  A BARE `gemini -m ...` is
  substituted down to 3.5-flash — the harness's per-session system settings file is
  what prevents that, so never judge model availability with a raw CLI probe.
  (lenient judge; needs the defect checklist for
  range).  Judge default `gemini-3.1-pro-preview` (`Settings.default_judge`; tracks use
  n_samples=1); flash + `--n 3` is the cheap fallback, pro for calibration/eval.
- Live runs under `runs/` (`e2e_*`): read-only reference material; never modify.
- Every model call is priced into the run's `telemetry/cost.jsonl` (`codeverse.cost.instrument`;
  `CV3D_COST_LEDGER=off` to disable).  `docs/COST.md` Part II has the measured cost controls.

## Commands
```
pip install -e /home/yipeng/3dcodeverse/harness   # once (entry points: 3dcodeverse, 3dcv)
cd /home/yipeng/3dcodeverse/harness
3dcodeverse doctor [--live] [--no-gpu] [--json]
3dcodeverse make "a mid-century wooden dining chair" --track static_object --language blender
3dcodeverse make "..." --profile economy|balanced|quality   # one dial: models, judge n, rounds, candidates, texture, ceilings (--profile == CV3D_PROFILE)
3dcodeverse make "..." --track static_object --language threejs --generator gemini-cli:gemini-3.7-flash
3dcodeverse make "..." --track articulated_object --language urdf_blender
3dcodeverse make "..." --track scene --language scene_threejs --rounds 2 --max-minutes 60
# There is NO cost ceiling and no --max-usd (owner, 2026-08-28: run first, count later).
# Cost is still priced onto every call — 3dcv cost, the ledger and the run record are
# untouched; --max-minutes / --rounds are what bound a run.
3dcodeverse make "neon rain on a window" --track graphics --language glsl_shader
3dcodeverse make "..." --image ref.png --candidates 3 --rounds 2 --dim height=0.45 --must "three legs" --texture --no-run
3dcv resume <slug> · 3dcv status <slug> · 3dcv render <slug> [--mode wire] · 3dcv judge <slug> [--model ... --n 3]
3dcv texture pass <slug> [--no-judge] · 3dcv texture scene-pack <slug> · 3dcv texture show <slug>
3dcv tools list · 3dcv tools measure --workspace runs/<slug> · 3dcv mcp --workspace runs/<slug>
3dcv cost <slug> · 3dcv cost --runs-dir bench/out/<battery> · 3dcv cost cache <slug>
3dcv cost prices [--stale] · 3dcv cost profiles · 3dcv cost estimate gemini:gemini-3.1-pro-preview --in 12000
3dcv flywheel export runs/ dataset/ [--pack --drop-duplicates --captions-dir d/] · 3dcv flywheel pairs runs/ pairs.jsonl
3dcv flywheel caption <slug> [--out dir] · 3dcv flywheel gallery runs/ gallery.html
3dcv gallery serve [ROOTS...] [--port 8765] [--reload] · 3dcv gallery build --out gallery.html [--embed]
3dcv bench run bench/prompts/static_objects_v1.yaml --generator ... --judge gemini:gemini-3.1-pro-preview
python -m codeverse.judges.calibration runs/<slug>... --model gemini:gemini-3.1-pro-preview --n 3 --out out/
python bench/complexity_report.py bench/out --recursive   # score-vs-complexity + $/complexity point (docs/COMPLEXITY.md)
python bench/compare_backends.py --prompts bench/prompts/compare_v1.yaml --arms harness:gemini-cli:gemini-3.6-flash,oneshot:claude-code --judge gemini:gemini-3.1-pro-preview --out bench/out/compare_v1
python -m pytest tests -q -m "not live"            # 2 905 tests, ~32 s (real Blender + headless Chrome + CadQuery)
python -m pytest tests -q -m "not live and not blender and not node"   # pure python: 2 775 tests, ~27 s
# both run PARALLEL by default (pytest-xdist, -n auto --dist worksteal, in pyproject addopts).
# A nested pytest inside a test MUST pass -n0 or it forks another full set of workers.
```

## Reference material (ideas only — never copy code)
`/home/yipeng/3dcodeverse_refs/_reports/*.md` — deep reads of astra3d-brilliana,
scene_multifile_graphics, opentopos, articraft, img2threejs, SpatialClaw, the owner's
3dcodeverse_data scripts, and a skills/pitfalls research report.
