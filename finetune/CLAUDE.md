# finetune/ (component notes for agents)

Training-data preparation and LLM fine-tuning for 3DCodeVerse. Read `README.md` here and
`docs/REPORT.md` (conclusions and pitfalls) first.

- Training is **unpatched LLaMA-Factory** driven by `configs/lf/*.yaml`; keep the upstream framework
  external and add a recipe plus the corresponding dataset registry entries.
- `llamafactory/` is the canonical home for this project's LF data conversion, QC, pair builders,
  mix sampling, and publication scripts. It is not vendored framework code. Edit implementations there;
  the six Python entries in `scripts/` are compatibility forwarders.
- Configs stay in `configs/`, environment snapshots in `env/`, and train/export/orchestration launchers
  in `scripts/`. General source curation and the contributor CLI belong in `../toolkits/`.
- Evaluate generated code with `../eval/llm/`: 3DCodeBench, held-out dialect suites, and pass@N. Follow
  its protocol and decoding defaults. Historical stop-DPO generation scripts are no longer distributed; the retained recipes
  require prepared preference datasets.
- Workstation paths remain in `env.sh`, launchers, configs, and data tools. Inspect them before running;
  editing `env.sh` alone is insufficient. Current local training recipes also require external data
  registries not fully represented by `data_specs/dataset_info.json`.
- Keep `docs/REPORT.md` and `llm_finetune_exps.md` (the historical conclusions and run log) intact when
  reorganizing code. Docs are in English; the original study notes (`docs/REPORT_FULL.md`) are in git history.
