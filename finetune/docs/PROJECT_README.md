# llm-ft — fine-tuning code LLMs for text → Blender-Python (3D) on dgx03

Everything lives under `/wekafs/ict/hx_624` (home is full). GPUs allowed: **0–3 only** (2/3 are often busy with another user's jobs → check `nvidia-smi`).

## Environments
| env | purpose | how |
|---|---|---|
| `llmft` (conda) | data prep, HF generation eval, metrics | `source llm-ft/env.sh` |
| `lf` (conda) | **LLaMA-Factory** training (`/wekafs/ict/hx_624/tools/LLaMA-Factory`) | `scripts/lf_train.sh` activates it |
| Blender 5.0.1 | executes generated scripts headlessly | `/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender` |

Anaconda 2026.07 is at `/wekafs/ict/hx_624/anaconda3` (already `conda init`-ed in `~/.bashrc`).

## Data
* Raw: `/wekafs/ict/hx_624/data/` — `3dcodebench/` (212 test tasks, GT GLB), `3dcodeverse/*.parquet` (bioinspired3d, factories, …), `thingiverse_5_0/`, `3dcodeverse/blender_distill/`.
* `scripts/prep_data.py` → `data/sft_v1/{train,val}.jsonl` (+ `bench_prompts.jsonl`, `stats.json`). The 212 benchmark factories are **excluded** from training (no contamination); only the 31 non-overlapping factories are used.
* `scripts/to_llamafactory.py` → `data/lf/*.json` + `dataset_info.json` (sharegpt format for LLaMA-Factory).

## Train (LLaMA-Factory)
```bash
GPUS=0,1,2 scripts/lf_train.sh configs/lf/qwen35_9b_lora_sft.yaml      # LoRA
GPUS=0,1,2 scripts/lf_train.sh configs/lf/qwen35_9b_full_sft.yaml      # full FT, DeepSpeed ZeRO-3
```
(`scripts/train_sft.py` + `scripts/launch_sft.sh` are an older plain-TRL alternative, kept for reference.)

## Evaluate on 3DCodeBench (212 tasks)
```bash
GPUS=0,1 eval/run_eval.sh <model_dir> eval/out/<name> [--no_think] [--batch_size 16]
```
= `eval/generate.py` (HF batched greedy generation, sharded over GPUs) → `eval/run_bench.py` (runs every `code.py` in headless Blender via `eval/blender_runner.py`, exports GLB) → `eval/metrics.py` (Chamfer / F-score vs GT GLB, best of 4 yaw rotations). Results: `eval/out/<name>/summary.json`.
Sanity: re-running the *reference* scripts through the harness gives Chamfer≈0.016, F@0.05≈1.0 (`eval/ref_oracle`).

## Experiment tooling (added 2026-08-21)
* `scripts/make_lora_cfg.py NAME DATANAME [--epochs --lr --rank --cutoff]` → `configs/lf/lora_NAME.yaml`
* `scripts/run_exp.sh <GPU[,GPU]> NAME` → train LoRA → merge (`lf_export.sh`) → vLLM generation → Blender exec + metrics (background) → `eval/out/qwen35_9b_lora_NAME/summary.json`
* `scripts/queue.sh <GPU> NAME1 NAME2 …` → run experiments sequentially on one GPU
* `scripts/ckpt_sweep.sh <GPU> <run_dir> NAME step1 step2 …` → evaluate intermediate adapter checkpoints
* `scripts/run_full_ft*.sh` → full-parameter ZeRO-3 SFT orchestration (3 GPUs, or 2 GPUs + CPU offload via `configs/lf/ds_z3_offload.json`)
* `eval/run_eval_vllm.sh <model> <out>` → vLLM-based eval (≈3k tok/s, 212 prompts in ~1.5 min); `eval/compare.py`, `eval/analyze_runs.py` → tables/analysis
* Results + conclusions: `REPORT.md`

## Multi-dialect tooling (added 2026-08-21 evening)
* `scripts/build_multidialect.py` → `data/multidialect/<dialect>/{train,test}.jsonl` + `report.json` (QC of every 3DCodeVerse subset; dialects: blender, cadquery, openscad, glsl)
* `scripts/build_multidialect_train.py` → token-filtered subsets `data/md_<dialect>` + `data/md_mixed` (LF datasets `md_*`)
* Executors: `eval/dialect_runners.py` (CadQuery via `cadquery` 2.8 in env `llmft`; OpenSCAD AppImage at `tools/openscad/squashfs-root/AppRun`; GLSL compile via conda-forge `glslangValidator`); Blender via `eval/blender_runner.py`
* `eval/run_dialect_eval.sh <model> <name> [dialects]` → vLLM generation + execution + reference-based F-score per dialect → `eval/out/md_<name>_<dialect>/summary.json`
* `scripts/run_md_exp.sh <GPU> md_<dialect>` → LoRA train → 3DCodeBench + 4-dialect eval

## 数据复用（2026-08-22）
- 各语言清洗结果固定在 `data/multidialect/<dialect>/{train,test}.jsonl`，所有 md_* 配比脚本（`scripts/build_md_big.py` / `build_md_xl.py`）只从这里取样；
- 每条样本的 token 数缓存在同名 `.ntok` 文件（`scripts/cache_ntok.py` 一次生成，`scripts/ntok_cache.py::load_ntok` 读取），改配比/上限不再重新 tokenize；
- LLaMA-Factory 的 `tokenized_path`（`data/lf/tokenized_<name>_len8192_packed`）缓存打包后的数据集，同一数据+cutoff 的后续训练直接复用。
