# 3dcodeverse — standing context

Backend harness (python package `codeverse`, CLI `3dcodeverse` / short alias `3dcv`,
repo path `/home/yipeng/3dcodeverse/harness`) for LLM-written **raw** 3D code:
Blender bpy · CadQuery · Three.js · URDF · GLSL / OpenGL, across four tracks
(`static_object`, `articulated_object`, `scene`, `graphics`), with pluggable
backends (Gemini/Anthropic/OpenAI APIs + gemini-cli / claude-code / codex /
antigravity / in-process api-agent / single-shot), an optional text-to-image
texture pass, and a data-flywheel record per run.

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
4. Typed everything; no regex-on-id control flow; no god files (≤ ~400 lines).
5. Cheap first: lint → build → gates → montaged views → VLM.  Budgets are hard.
6. Every round = a git commit of `src/`; every call = a `Usage`; every run = `record.json`.
7. No wrapper re-centres or grounds the object: export **as authored**, warn in the
   build, let `check_contract`/`check_connectivity` gate it.

## Supported versions (what other people can run this on)
python **3.12+** · node **20.6+** · Blender 4.2+ · Linux x86_64.  Developed on 3.13 /
node 24; **CI runs the floor (3.12) and only the floor** — one job that tests exactly what
`requires-python` claims, so the claim cannot drift untested.  3.12 is the floor because it
is what Ubuntu 24.04 LTS ships; being >= 3.11 it also needs no compatibility shim
(`StrEnum`, `datetime.UTC` and `tomllib` are stdlib from 3.11, which is why
`codeverse/_compat.py` was deleted).  The floor lives in `pyproject` `requires-python` +
ruff `target-version` + `scripts/setup.sh`, and `spatial/node.py:NODE_MIN` +
`runtime_js/package.json` `engines` for node, pinned together by
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
- Main test model: `gemini-3.7-flash` (lenient judge; needs the defect checklist for
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
3dcodeverse make "..." --track scene --language scene_threejs --rounds 2 --max-usd 3
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
python bench/compare_backends.py --prompts bench/prompts/compare_v1.yaml --arms harness:api-agent:gemini:gemini-3.7-flash,oneshot:claude-code --judge gemini:gemini-3.1-pro-preview --out bench/out/compare_v1
python -m pytest tests -q -m "not live"            # ~860 offline tests; add "and not blender and not node" for pure python
```

## Reference material (ideas only — never copy code)
`/home/yipeng/3dcodeverse_refs/_reports/*.md` — deep reads of astra3d-brilliana,
scene_multifile_graphics, opentopos, articraft, img2threejs, SpatialClaw, the owner's
3dcodeverse_data scripts, and a skills/pitfalls research report.
