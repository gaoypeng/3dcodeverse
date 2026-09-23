# 3dcodeverse harness

A backend harness in which LLMs write **raw, executable 3D code** — Blender `bpy`,
CadQuery, Three.js, URDF, GLSL / OpenGL — and the harness plans, builds, measures,
renders, judges, refines, textures and records every run as data-flywheel material.

* **Tracks:** `static_object` · `articulated_object` · `scene` · `graphics`
* **Languages:** `blender` · `cadquery` · `threejs` · `urdf_blender` ·
  `scene_threejs` · `glsl_shader` · `opengl_python`
* **Backends:** Gemini / Anthropic / OpenAI chat models; coding agents `gemini-cli`,
  `claude-code`, `codex`, `agy` (Antigravity) — always a vendor's CLI — and
  `single-shot` generation on any chat model.
* **Spatial tools** (direct, or over the MCP server `3dcode` for the agentic CLIs): build,
  measure, render, section, connectivity/contract checks, joint sweeps, scene and GL probes —
  the list is `docs/ARCHITECTURE.md` §5.
* **Judging:** rubric VLM judge (default `gemini-3.1-pro-preview`) on labelled 2×2
  montages with binary defect checklists; code-computed scores, floors and caps from
  deterministic gates; pairwise, reference and likeness judges; best-of-N candidates;
  a calibration harness (`addons/calibration.py`).
* **Texturing:** optional text-to-image pass — VLM material plan → seamless tiles →
  world-metre UVs → `object_textured.glb`, shipped only when a before/after judge
  gate agrees; scene texture packs for prompts.
* **Flywheel:** git-versioned `src/` per round, `record.json`, sample export with
  quality tiers + dedupe (+ parquet, tar locators), preference/repair/cross-backend
  pairs, captions, sqlite index, HTML gallery.
* **Looking at results:** `3dcode gallery serve` — a local page over `runs/` +
  `eval/bench/out/*/runs` with filters, a per-filter summary strip and a detail page per run;
  it serves the run directories too, so every link (sheet, renders, `src/`, `object.glb`
  in an orbit viewer, `record.json`) actually opens.  `3dcode gallery build --embed` writes
  the same page as one shareable file.

## Architecture

Every run is **spec → plan → skeleton → [scene stages] → baseline round (best-of-N
optional) → refine rounds → finalise → record**, with an optional post-hoc texture
pass.  The package tree:

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
                   data dirs: blender cadquery threejs urdf scene_threejs glsl_shader opengl_python
  agents/          CodingAgent protocol + the vendor-CLI backends (gemini-cli, claude-code,
                   codex, antigravity): sessions, watchdog clocks, transcripts
  models/          ChatModel + gemini/anthropic/openai adapters; key-pool retry machine,
                   streaming with stall detection, IPv4-pinned transport, pricing, health
  spatial/         the measurement/render toolbox behind every gate and MCP tool
  judges/          rubric VLM judge on labelled montages, pairwise, reference/likeness
  reference.py     reference grounding: synthesis, plausibility gate, proportions, diff
  texturing/       material plan → seamless tiles → world-metre UVs → object_textured.glb
  cost/            append-only ledger, metering, per-block tallies, profiles, pre-call estimates
  skills/          typed skill routes + materialisation + read telemetry
  record/          what every run writes: record.json, deliverable/, telemetry
  addons/          optional tools that READ finished runs — nothing a run needs:
                   gallery (the runs browser), dataset (export, tiers, preference/repair pairs,
                   captions, sqlite index), costreport (`3dcode cost`), calibration, skill_targets
  cli/             the typer CLI · doctor.py  the environment checks behind `3dcode doctor`
../eval/bench/     the evaluation harness around the harness: run_bench, compare_backends
                   (A/B matrix), ab_plan, infra-failure classification
runtime_js/        node side: three@0.182 + headless-Chrome render/probe hosts
```

Design laws, the full package map and what each stage does: `docs/ARCHITECTURE.md`.

**Supported versions:** python **3.13** and node **20.6+** (Linux x86_64; Blender
4.2+ optional).  Developed and measured on python 3.13 / node 24; there is no CI — the offline suite and
`ruff` run locally before every push.  See `docs/INSTALL.md` §2.1.

```bash
bash setup.sh     # install everything + run doctor (idempotent; see docs/INSTALL.md)
# ...or by hand:
pip install -e '.[all,dev]'    # entry points: 3dcodeverse, 3dcode
(cd runtime_js && npm ci)      # three@0.182 + puppeteer 24 (chrome → ~/.cache/puppeteer)
3dcode doctor
3dcode make "a mid-century wooden dining chair" --track static_object --language blender
3dcode make "a kitchen cabinet with one door and a drawer" --track articulated_object --language urdf_blender
3dcode make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs
3dcode make "neon cyberpunk rain on a window" --track graphics --language glsl_shader
3dcode gallery serve             # browse every run at http://127.0.0.1:8765/ (docs/RUNBOOK.md §4)
python -m pytest tests -q -m "not live"
```

What a run costs and how long it takes: `docs/COST.md` (measured) and `docs/RUNBOOK.md` §2
(expected, per track).

Docs: `docs/INSTALL.md` (install / prerequisites / doctor troubleshooting) ·
`docs/ARCHITECTURE.md` (design + what a run does) · `docs/INTERFACES.md`
(signatures) · `docs/RUNBOOK.md` (operate / extend) · `docs/DECISIONS.md` (ADRs) ·
`eval/docs/EVAL.md` (evaluation protocol + judge calibration) · `CLAUDE.md` (working rules).

Effect library studies: [Graphics Lab](examples/graphics_lab/README.md).
