# 3dcodeverse

The 3dcodeverse project. Top-level layout:

- **`harness/`** — the 3dcodeverse harness (python dist `3dcodeverse`, import
  `codeverse`, CLI `3dcodeverse` / `c3v`): the backend that turns prompts into
  raw 3D code (Blender bpy / CadQuery / Three.js / URDF / GLSL) through a
  plan → generate → gate → render → judge → refine loop, and emits
  data-flywheel records.  See `harness/README.md`.

Other large components (datasets, papers, web) live in their own top-level
folders.
