# scripts/

Everything here is generic. Per-experiment drivers were one-offs bound to a single config; they are in
`archive/scripts/` (kept on disk, not synced).

| script | what it does |
|---|---|
| `lf_train.sh <config>` | run a LLaMA-Factory config on `GPUS=<ids>` |
| `train_when_free.sh <config> [need_mib] [log]` | wait for a card that is *still* free at launch, retry if another user takes it mid-load |
| `free_gpus.sh [need_mib] [gpu_list]` | list cards with that much free memory (the box is shared) |
| `lf_export.sh <config>` / `export_lora.sh <run_dir> [base]` | merge a LoRA adapter into `<run_dir>/merged` |
| `stop_dpo_pairs.sh` | build termination preference pairs (`TAG`, `BASE_MODEL` env) |
| `stop_dpo_round.sh <base_model> <tag>` | one full stop-DPO round: pairs → train → merge |
| `supervisor2.sh` | keeps the queue in `queue.txt` running and warns on idle cards |
| `sync_repo.sh` | mirror scripts/configs/eval + REPORT.md to the 3dcodeverse repo |

Data tooling: `build_pairs_3dcodebench.py` (pair builder), `convert_subdirs.py`, `shrink_pair_images.py`,
`audit_pairs.py` (**run before publishing** — scans every row for absolute paths, missing images and
`<image>`-count mismatches), `upload_pairs.py`, `drop_old_pair_trees.py`, `verify_unflattened_glsl.py`.
