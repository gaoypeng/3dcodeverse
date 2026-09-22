# eval/llm (component notes for agents; was `3dcodeverse_eval`)

Standalone LLM/VLM evaluation for 3D code (Blender-Python / CadQuery / OpenSCAD / GLSL / three.js). Read
`README.md`, then `docs/metrics.md` and `docs/protocol.md`. Do not import from `../eval` or `../scripts`;
this package is the portable re-implementation (provenance in `docs/inventory_*.md`).

- **Paths**: never hardcode; everything goes through `config.py` (`C3D_EVAL_DATA`, `C3D_TOOLS`, `C3D_BLENDER` …).
  Large assets live under `$C3D_EVAL_DATA` (hub/, refs/, out/), never in git. `data/prompts/*.jsonl` are committed.
- **Environment**: conda env `cv3d-eval` (`~/miniconda3/envs/cv3d-eval/bin/python`): vLLM 0.28 + torch cu130
  (RTX 5090 works), cadquery, playwright, trimesh, transformers. Blender 5.0.1 at `~/.local/bin/blender-5.0`
  (bundled python has scipy 1.17.1 + shapely), OpenSCAD AppImage + glslang under `~/3dcodeverse_data/tools`.
  `python -m llm.config` is the doctor.
- **Protocols**: keep the report protocol (`3dcodebench_text`, `heldout_*`) byte-identical to
  `finetune/docs/REPORT.md` so numbers stay comparable; the official protocol (`3dcodebench_official_*`)
  mirrors `~/nips2026_paper/3dcodebench` (prompts, renderer, SigLIP-2/DINOv3, squared unit-sphere Chamfer).
  Changing a system prompt or metric = a new suite name, not an edit.
- **Long-output dialects** (OpenSCAD / GLSL / three.js): T=0.7 is the primary protocol (greedy degenerates);
  always report `truncated`. Compare models only within one batch (noise floor in `docs/metrics.md` §6).
- **Statuses** are uniform: OK / EMPTY / FAIL / CRASH / TIMEOUT / MISSING; `*_mean_all` uses `n_ref_ok` as the
  denominator (reference failures are not charged to the model).
- Tests: `~/miniconda3/envs/cv3d-eval/bin/python -m pytest llm/tests -q` (needs the tools for the executor tests).
