# 3dcodeverse harness

A backend harness in which LLMs write **raw, executable 3D code** — Blender `bpy`,
CadQuery, Three.js, URDF, GLSL / OpenGL — and the harness plans, builds, measures,
renders, judges, refines and records every run as data-flywheel material.

* **Tracks:** `static_object` · `articulated_object` · `scene` · `graphics`
* **Languages:** `blender` · `cadquery` · `threejs` · `urdf_blender` ·
  `scene_threejs` · `scene_blender` · `glsl_shader` · `opengl_python`
* **Backends:** Gemini / Anthropic / OpenAI chat models; coding agents `claude-code`,
  `codex`, `agy` (Antigravity) — always a vendor's CLI — and
  `single-shot` generation on any chat model.
* **Spatial tools** (direct, or over the MCP server `3dcode` for the agentic CLIs): build,
  measure, render, section, connectivity/contract checks, joint sweeps, scene and GL probes —
  the list is `docs/ARCHITECTURE.md` §5.
* **Judging:** rubric VLM judge (default `gemini-3.1-pro-preview`) on labelled 2×2
  montages with binary defect checklists; code-computed scores, floors and caps from
  deterministic gates; pairwise, reference and likeness judges; best-of-N candidates;
  a calibration harness (`addons/calibration.py`).
* **Flywheel:** git-versioned `src/` per round, `record.json`, sample export with
  quality tiers + dedupe (+ parquet, tar locators), preference/repair/cross-backend
  pairs, captions, HTML gallery.
* **Looking at results:** `3dcodeverse gallery serve` — a local page over `runs/` +
  `eval/bench/out/*/runs` with filters, a per-filter summary strip and a detail page per run;
  it serves the run directories too, so every link (sheet, renders, `src/`, `object.glb`
  in an orbit viewer, `record.json`) actually opens.  `3dcodeverse gallery build --embed` writes
  the same page as one shareable file.

## Architecture

Every run is **spec → plan → skeleton → [scene stages] → baseline round (best-of-N
optional) → refine rounds → finalise → record**.  The package tree:

```
codeverse3d/
  contracts/       typed pydantic contracts: Track/Language/Usage/Budget/Backends,
                   Spec, plans, artifacts, run record, AgentJob
  conventions.py   frames, units, views, naming, tolerances (THE constants source)
  config.py        Settings (C3D_* env + config.yaml) · workspace.py  run-dir layout + git snapshots
  proc.py          stdlib-only subprocess/JSON/JSONL primitives, run lock, fan-out (a leaf)
  orchestrator.py  the round loop's library: stage runner + resume state, round knobs, the clock
  tracks/          the four track pipelines + the round loop, the one planner loop, generation strategies
                   (vendor-CLI agent / single-shot), repair, best-of-N, skills hook
  languages/       one merged module per language (lint → skeleton → runtime) beside its
                   data dirs: blender cadquery threejs urdf scene_threejs scene_blender glsl_shader opengl_python
  agents/          CodingAgent protocol + the vendor-CLI backends (claude-code,
                   codex, antigravity): sessions, watchdog clocks, transcripts
  models/          ChatModel + gemini/anthropic/openai adapters; key-pool retry machine,
                   streaming with stall detection, IPv4-pinned transport, pricing, health
  spatial/         the measurement/render toolbox behind every gate and MCP tool
  judges/          rubric VLM judge on labelled montages, pairwise, reference/likeness
  reference.py     a user `--image` against the render: proportion check, mismatch diff
  cost/            append-only ledger, metering, per-block tallies, pre-call estimates
  skills/          typed skill routes + materialisation + read telemetry
  record/          what every run writes: record.json, deliverable/, telemetry
  addons/          optional tools that READ finished runs — nothing a run needs:
                   gallery (the runs browser), dataset (export, tiers, preference/repair pairs,
                   captions), costreport (`3dcodeverse cost`), calibration, skill_targets
  cli/             the typer CLI · doctor.py  the environment checks behind `3dcodeverse doctor`
../eval/bench/     the evaluation harness around the harness: run_bench, compare_backends
                   (A/B matrix), ab_plan, infra-failure classification
runtime_js/        node side: three@0.182 + headless-Chrome render/probe hosts
```

Design laws, the full package map and what each stage does: `docs/ARCHITECTURE.md`.

**Supported versions:** python **3.13** and node **20.6+** (Linux x86_64; Blender
5.2 LTS optional).  Developed and measured on python 3.13 / node 24; there is no CI — the offline suite and
`ruff` run locally before every push.  See `docs/INSTALL.md` §2.1.

```bash
bash setup.sh     # python package + runtime_js + headless Chrome + the agy / claude / codex CLIs at their latest + doctor
# ...or by hand:
pip install -e '.[all,dev]'    # entry point: 3dcodeverse
(cd runtime_js && npm ci)      # three@0.182 + puppeteer 24 (chrome → ~/.cache/puppeteer)
curl -fsSL https://antigravity.google/cli/install.sh | bash      # agy, the default generator's CLI (`agy update` later)
npm i -g @anthropic-ai/claude-code@latest @openai/codex@latest   # optional generators claude-code / codex (local subscriptions)
export GEMINI_API_KEYS="key1,key2"                               # agy runs in Gemini-API mode on this key pool (docs/INSTALL.md §8)
3dcodeverse doctor
3dcodeverse make "a mid-century wooden dining chair" --track static_object --language blender
3dcodeverse make "a kitchen cabinet with one door and a drawer" --track articulated_object --language urdf_blender
3dcodeverse make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs
3dcodeverse make "neon cyberpunk rain on a window" --track graphics --language glsl_shader
3dcodeverse gallery serve             # browse every run at http://127.0.0.1:8765/ (docs/RUNBOOK.md §4)
python -m pytest tests -q -m "not live"
```

Expected cost and time per track: `docs/RUNBOOK.md` §2. Inspect actual run costs with
`3dcodeverse cost <slug>` and a call's expected price with `3dcodeverse cost estimate`.

Docs: `docs/INSTALL.md` (install / prerequisites / doctor troubleshooting) ·
`docs/ARCHITECTURE.md` (design + what a run does) · `docs/RUNBOOK.md` (operate / extend) ·
`eval/docs/EVAL.md` (evaluation protocol + judge calibration) · `CLAUDE.md` (working rules).
