# 3dcodeverse — standing context

Backend harness (python package `codeverse`, CLI `c3v`) for LLM-written **raw** 3D
code: Blender bpy · CadQuery · Three.js · URDF · GLSL, across three tracks
(`static_object`, `articulated_object`, `scene`), with pluggable backends
(Gemini/Anthropic/OpenAI APIs + gemini-cli / claude-code / codex / antigravity /
in-process api-agent) and a data-flywheel record per run.

Read `docs/ARCHITECTURE.md` (design + package map + authoring contracts) and
`docs/INTERFACES.md` (binding cross-package signatures) before changing anything.

## Laws (do not break)
1. Generated code is raw language — never an SDK/helper import; the harness owns
   wrappers/exporters (`codeverse/languages/*/wrappers`, `runtime_js/`).
2. `codeverse/contracts/` is data-only and shared; `conventions.py` is the only
   place that states frames/units/naming.  Import, never restate.
3. Deterministic gates/measurements run by the harness; VLM only for perception.
   Score is computed in code from rubric weights; caps/floors are explicit.
4. Typed everything; no regex-on-id control flow; no god files (≤ ~400 lines).
5. Cheap first: lint → build → gates → 4-view sheet → VLM.  Budgets are hard.
6. Every round = a git commit of `src/`; every call = a `Usage`; every run = `record.json`.

## Environment (this machine)
- Gemini keys: `~/.config/astra3d/gemini_keys.env` (22 keys) → `get_settings().gemini_api_keys`.
- Blender 5.0.1 headless: `~/.local/bin/blender-5.0` (always `--factory-startup`; clear scene).
- Node 24 + `runtime_js/node_modules` (three@0.182, puppeteer; chrome cached).  Headless
  Chrome WebGL uses the GPU on WSL2 with `--use-angle=gl-egl` + Mesa d3d12 env (see
  `runtime_js/gpu_launch.cjs`); SwiftShader fallback.
- CLIs: `gemini` 0.53 (api-key auth + `dynamicModelConfiguration` via system settings file,
  else it silently substitutes models), `codex` 0.147, `claude` 2.1, `agy` 1.1.5 — the last
  three run on local subscriptions: test lightly.
- Main test model: `gemini-3.7-flash`.  Judge default: `gemini-3.7-flash` (n_samples=2) or
  `gemini-3.1-pro-preview` for calibration runs.

## Commands
```
pip install -e /home/yipeng/3dcodeverse/harness   # once
c3v doctor
c3v make "a mid-century wooden dining chair" --track static_object --language blender
c3v make "..." --track articulated_object --language urdf_blender
c3v make "..." --track scene --language scene_threejs --generator gemini-cli:gemini-3.7-flash
c3v resume <slug> · c3v status <slug> · c3v render <slug> · c3v judge <slug>
c3v tools list · c3v mcp --workspace runs/<slug>
c3v flywheel export runs/ dataset/ · c3v bench run bench/prompts/static_objects_v1.yaml
python -m pytest tests -q -m "not live"
```

## Reference material (ideas only — never copy code)
`/home/yipeng/3dcodeverse_refs/_reports/*.md` — deep reads of astra3d-brilliana,
scene_multifile_graphics, opentopos, articraft, img2threejs, SpatialClaw, the owner's
3dcodeverse_data scripts, and a skills/pitfalls research report.
