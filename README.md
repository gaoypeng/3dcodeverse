# 3dcodeverse

The 3dcodeverse project. Top-level layout:

- **`harness/`** — the 3dcodeverse harness (python dist `3dcodeverse`, import
  `codeverse`, CLI `3dcodeverse` / `c3v`): the backend that turns prompts into
  raw 3D code (Blender bpy / CadQuery / Three.js / URDF / GLSL) through a
  plan → generate → gate → render → judge → refine loop, and emits
  data-flywheel records.  See `harness/README.md`.

- **`finetune/`** — LLM finetuning recipes (LoRA / full-parameter SFT, execution-
  and geometry-feedback DPO via unpatched LLaMA-Factory) and the execution-based
  evaluation stack (3DCodeBench + per-dialect executors) used to train open
  models on 3DCodeVerse data; results and conclusions in `finetune/docs/REPORT.md`.
  See `finetune/README.md`.

Other large components (datasets, papers, web) live in their own top-level
folders.
