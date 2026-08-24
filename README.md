# 3dcodeverse

The 3dcodeverse project. Top-level layout:

- **`harness/`** — the 3dcodeverse harness (python dist `3dcodeverse`, import
  `codeverse`, CLI `3dcodeverse` / `3dcv`): the backend that turns prompts into
  raw 3D code (Blender bpy / CadQuery / Three.js / URDF / GLSL / OpenGL) across
  four tracks (static objects, articulated objects, scenes, graphics) through a
  plan → generate → gate → render → judge → refine loop, with text-to-image
  texturing and data-flywheel records.  See `harness/README.md`.

  Install with `bash harness/scripts/setup.sh` (idempotent: python deps, the
  `runtime_js/` node deps, puppeteer's Chrome, then `3dcodeverse doctor`) —
  prerequisites, extras, keys, GPU notes and troubleshooting are in
  `harness/docs/INSTALL.md`.  Needs **python 3.10+** and **node 20.6+** on Linux
  (developed on 3.13 / node 24; CI runs both ends — `harness/docs/INSTALL.md` §2.1).

- **`finetune/`** — LLM finetuning recipes (LoRA / full-parameter SFT, execution-
  and geometry-feedback DPO via unpatched LLaMA-Factory) and the execution-based
  evaluation stack (3DCodeBench + per-dialect executors) used to train open
  models on 3DCodeVerse data; results and conclusions in `finetune/docs/REPORT.md`.
  See `finetune/README.md`.

Other large components (datasets, papers, web) live in their own top-level
folders.
