# 3dcodeverse (repo root)

**`harness/`** is the `3dcodeverse` plugin for Claude Code, Codex and Antigravity: skills, a judge
subagent and the `3dcode` command-line instruments (build, measure, render, gates, judge pack, score).
Read `harness/CLAUDE.md` + `harness/docs/ARCHITECTURE.md` before touching it.  The repo root is its
marketplace (`.claude-plugin/marketplace.json`).  Install: `bash harness/setup.sh`.  Tests live outside
the repo (`~/3dcodeverse_tests`); never add a `tests/` folder back under `harness/`.

**`flywheel/`** (`c3d_flywheel`, CLI `c3d-flywheel`) reads runs recorded by the old CLI harness (v0.0.x):
show/status, cost, gallery, dataset export/pairs/refine, pick.  It depends on the plugin, never the reverse.

**`eval/llm`** evaluates a bare LLM/VLM on 3D coding (no harness in the loop); read `eval/README.md`.

Other top-level folders are separate components: `toolkits/` owns the general data CLI and source curation;
`finetune/` owns training and its data preparation. The canonical LLaMA-Factory data scripts live in
`finetune/llamafactory/`; `finetune/configs/`, `finetune/env/`, and the training launchers in
`finetune/scripts/` retain their existing locations. Read `finetune/CLAUDE.md` before changing them.
