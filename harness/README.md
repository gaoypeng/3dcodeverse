# 3dcodeverse harness

A backend harness in which LLMs write **raw, executable 3D code** — Blender `bpy`,
CadQuery, Three.js, URDF, GLSL / OpenGL — and the harness plans, builds, measures,
renders, judges, refines, textures and records every run as data-flywheel material.

* **Tracks:** `static_object` · `articulated_object` · `scene` · `graphics`
* **Languages:** `blender` · `cadquery` · `threejs` · `urdf_blender` ·
  `scene_threejs` · `glsl_shader` · `opengl_python`
* **Backends:** Gemini / Anthropic / OpenAI chat models; coding agents `gemini-cli`,
  `claude-code`, `codex`, `agy` (Antigravity), the in-process `api-agent` tool loop, and
  `single-shot` generation on any chat model.
* **Spatial tools** (direct, MCP server `3dcv` for agentic CLIs, native tools for
  api-agent): build, measure, render_views/sheet, isolate, cross_section,
  check_connectivity, check_contract, compare_silhouette, joint_sweep, shader_probe,
  scene_probe, scene_views, gl_probe, gl_frames, texture_pass, texture_preview,
  read_cookbook.
* **Judging:** rubric VLM judge (default `gemini-3.1-pro-preview`) on labelled 2×2
  montages with binary defect checklists; code-computed scores, floors and caps from
  deterministic gates; pairwise, ranking and reference judges; best-of-N candidates;
  a calibration harness (`judges/calibration.py`).
* **Texturing:** optional text-to-image pass — VLM material plan → seamless tiles →
  world-metre UVs → `object_textured.glb`, shipped only when a before/after judge
  gate agrees; scene texture packs for prompts.
* **Flywheel:** git-versioned `src/` per round, `record.json`, sample export with
  quality tiers + dedupe (+ parquet, tar locators), preference/repair/trajectory
  pairs, captions, sqlite index, HTML gallery.
* **Looking at results:** `3dcv gallery serve` — a local page over `runs/` +
  `bench/out/*/runs` with filters, a per-filter summary strip and a detail page per run;
  it serves the run directories too, so every link (sheet, renders, `src/`, `object.glb`
  in an orbit viewer, `record.json`) actually opens.  `3dcv gallery build --embed` writes
  the same page as one shareable file.

**Supported versions:** python **3.13** and node **20.6+** (Linux x86_64; Blender
4.2+ optional).  Developed and measured on python 3.13 / node 24; there is no CI — the offline suite and
`ruff` run locally before every push.  See `docs/INSTALL.md` §2.1.

```bash
bash scripts/setup.sh     # install everything + run doctor (idempotent; see docs/INSTALL.md)
# ...or by hand:
pip install -e '.[all,dev]'    # entry points: 3dcodeverse, 3dcv
(cd runtime_js && npm ci)      # three@0.182 + puppeteer 24 (chrome → ~/.cache/puppeteer)
3dcv doctor
3dcv make "a mid-century wooden dining chair" --track static_object --language blender
3dcv make "a kitchen cabinet with one door and a drawer" --track articulated_object --language urdf_blender
3dcv make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs
3dcv make "neon cyberpunk rain on a window" --track graphics --language glsl_shader
3dcv gallery serve             # browse every run at http://127.0.0.1:8765/ (docs/RUNBOOK.md §4)
python -m pytest tests -q -m "not live"
```

Observed with `gemini-3.7-flash` end to end: chair (blender) 0.67 → 0.74 in 2 rounds,
12 min, $0.84; cabinet (URDF) 0.68 → 0.93, 12.5 min, $0.84; park bench (threejs via
gemini-cli) 0.64 → 0.89, 36 min, $0.68; garden scene 0.56 → 0.60 over 2 refine rounds,
45 min, $2.90; neon-rain shader (graphics, single-shot) 0.79 first round, 2 min, $0.05;
best-of-2 stool 0.56 → 0.61, 7.6 min, $0.24.

Docs: `docs/INSTALL.md` (install / prerequisites / doctor troubleshooting) ·
`docs/ARCHITECTURE.md` (design + what a run does) · `docs/INTERFACES.md`
(signatures) · `docs/RUNBOOK.md` (operate / extend) · `docs/DECISIONS.md` (ADRs) ·
`docs/EVAL.md` (evaluation protocol + judge calibration) · `CLAUDE.md` (working rules).
