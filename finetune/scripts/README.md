# scripts/ — launchers and compatibility entrypoints

Training/export launchers and workstation orchestration stay here. Canonical LLaMA-Factory data
implementations live in [`../llamafactory/`](../llamafactory/); see its README for the script catalog.

| script | what it does |
|---|---|
| `lf_train.sh <config>` | run a LLaMA-Factory config on `GPUS=<ids>` |
| `train_when_free.sh <config> [need_mib] [log]` | wait for a free card and retry a training launch after a detected OOM |
| `free_gpus.sh [need_mib] [gpu_list]` | list cards with the requested free memory |
| `lf_export.sh <config>` / `export_lora.sh <run_dir> [base]` | merge a LoRA adapter into `<run_dir>/merged` |

These launchers retain workstation-specific paths; adapt them before use.
Historical workstation queues, synchronization and stop-DPO orchestration are no longer distributed.

The following six Python files are thin CLI forwarders, preserving existing command paths while
running the implementation in `../llamafactory/`:

- `audit_pairs.py`
- `build_pairs_3dcodebench.py`
- `convert_subdirs.py`
- `shrink_pair_images.py`
- `upload_pairs.py`
- `verify_unflattened_glsl.py`

Use `python llamafactory/<script>.py ...` from `finetune/` for new workflows. Historical per-experiment
drivers were archived outside this checkout; their absence is not filled by these compatibility paths.
