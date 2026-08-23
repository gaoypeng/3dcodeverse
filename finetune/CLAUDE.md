# finetune/ (component notes for agents)

LLM finetuning + execution-based eval for 3DCodeVerse. Read `README.md` here and `docs/REPORT.md` (conclusions + pitfalls) first.
- Training is **unpatched LLaMA-Factory** driven by `configs/lf/*.yaml`; do not fork the framework — add a YAML + a dataset entry.
- Evaluate by *executing* the code (`eval/`): 3DCodeBench (Blender → Chamfer/F vs GT), per-dialect held-out sets, pass@N. Greedy decoding collapses on boilerplate-heavy dialects (OpenSCAD/GLSL) — use sampling there.
- Absolute paths point at the original machine; adapt `env.sh` + YAML path keys + tool paths in `eval/*runner*.py` before running.
