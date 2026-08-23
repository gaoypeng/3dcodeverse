# finetune/ — LLM finetuning recipes + execution-based evaluation for 3DCodeVerse

Everything needed to reproduce the Qwen3.5-9B finetuning study on 3DCodeVerse
(text → Blender-Python / CadQuery / OpenSCAD / GLSL / three.js code), and the
conclusions in `docs/REPORT.md`. The harness (`../harness`) generates data with
API models; this folder trains and evaluates open models on that data.

**Nothing in LLaMA-Factory is patched** — all training is plain
`llamafactory-cli` YAML (`configs/lf/`) + ShareGPT JSON registered in
`data_specs/dataset_info.json`. Pinned environments are in `env/`.

## Layout

| path | what |
|---|---|
| `scripts/` | data builders (`prep_data.py`, `build_multidialect*.py`, `build_md_big.py`, `build_md_xl.py`, `build_md_bal.py`, `build_boot_data.py`, `cache_ntok.py`), thin launchers around LLaMA-Factory (`lf_train.sh`, `lf_export.sh`, `make_lora_cfg.py`), experiment pipelines train→merge→eval (`run_exp.sh`, `run_md_exp.sh`, `run_full_ft*.sh`, `*_ckpt_sweep.sh`), execution-feedback / geometry-feedback DPO rounds (`run_dpo_exec.sh`, `dpo_round2.sh`, `md_dpo_round.sh`, `md_big_dpo_round.sh`, `md_xl_dpo_round.sh`, `geo_dpo_build.sh`), bootstrapping (`self_train_round*.sh`), report tooling (`build_report_html.py`, `contact_sheet_3dcodebench.py`) |
| `configs/lf/` | one LLaMA-Factory YAML per experiment: LoRA SFT (`lora_*.yaml`), full-parameter ZeRO-3 SFT (`qwen35_9b_full_sft*.yaml`, `full_md_bal.yaml`, `ds_z3_offload.json`), DPO (`dpo_*.yaml`), LoRA merge/export (`export_*.yaml`) |
| `eval/` | vLLM generation (`generate_vllm.py`), 3DCodeBench runner (`run_bench.py`, `blender_runner.py`), geometry metrics Chamfer / F-score vs GT (`metrics.py`), pass@N / best-of-N (`best_of_n.sh`, `pass_at_n.py`), per-dialect executors (`dialect_runners.py`: CadQuery, OpenSCAD, glslangValidator; `threejs_runner.py`: Playwright headless Chromium), held-out dialect evals (`dialect_eval.py`, `blender_dialect_eval.py`, `threejs_eval.py`, `run_dialect_eval.sh`), renders for qualitative checks (`render_glb.py`) |
| `data_specs/dataset_info.json` | LLaMA-Factory dataset registry for every SFT/DPO set used (ShareGPT format; the JSON files themselves are rebuilt by `scripts/`) |
| `env/requirements-{llmft,lf,vllm}.txt` | pip freezes of the three conda envs (data/eval, LLaMA-Factory training, vLLM inference) |
| `env.sh` | shell env (caches, secrets file) used by all scripts |
| `docs/REPORT.md` | full experiment report (setup, data audit, ablations, DPO, multi-dialect, scale-up, training-amount curves, qualitative checks); `docs/PROJECT_README.md` is the original working README; `docs/assets/` renders |

## Install & background
* **`docs/SETUP.md`** — step-by-step environment install (three conda envs with exact pins, LLaMA-Factory commit, flash-attn / fla / triton, vLLM, Blender / OpenSCAD / glslang / Playwright, caches on a network FS, smoke tests).
* **`docs/LLAMA_FACTORY.md`** — how this project uses LLaMA-Factory (unpatched): dataset registry, template, stages (SFT / DPO), LoRA vs full + ZeRO-3, packing, launch/merge scripts, an annotated minimal YAML, and the pitfalls.
* **`docs/REPORT.md`** — the experiment report (results + conclusions).

## Reproducing (short version)

1. Envs (see `docs/REPORT.md` §1 for the pitfalls): `lf` = LLaMA-Factory (editable, upstream, unpatched) + torch 2.8.0+cu128 + flash-attn 2.8.3 + flash-linear-attention 0.5.2 + **triton 3.7.1** (Qwen3.5 GDN kernels on Hopper) + deepspeed; `vllm` = vLLM 0.27; `llmft` = data/eval (trimesh, cadquery, playwright, …). Blender 5.0.1 headless and an OpenSCAD AppImage are called by path.
2. Data: only `metadata.parquet` of each 3DCodeVerse subset is needed. `scripts/build_multidialect.py` → `data/multidialect/<dialect>/{train,test}.jsonl`; `scripts/build_md_*.py` build the mixes (token-length filter ≤8192, cached by `scripts/cache_ntok.py`); `scripts/to_llamafactory.py` writes ShareGPT JSON + `dataset_info.json`.
3. Train: `GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_md_xl.yaml gradient_accumulation_steps=2` (LoRA r64, cutoff 8192, packing, FA2) → `scripts/lf_export.sh configs/lf/export_md_xl.yaml` to merge.
4. Evaluate: `GPUS=0 eval/run_eval_vllm.sh <merged> eval/out/<name> --no_think --max_new_tokens 6144` (3DCodeBench: vLLM gen → Blender exec → Chamfer/F vs GT) and `GPUS=0 eval/run_dialect_eval.sh <merged> <name>` (held-out CadQuery/OpenSCAD/GLSL/Blender). Decode OpenSCAD/GLSL with sampling (T=0.7), not greedy.
5. Execution-feedback DPO on top of any SFT model: `scripts/md_xl_dpo_round.sh` (sample → run in each dialect's executor → (OK, FAIL) pairs → `stage: dpo`).

Paths are absolute for the machine the study ran on (`/wekafs/ict/hx_624/...`); adapt `env.sh`, the `*_dir`/`model_name_or_path` keys in `configs/lf/*.yaml`, and the tool paths at the top of `eval/*runner*.py` / `scripts/*.sh` before running elsewhere.

## Headline results (3DCodeBench, 212 tasks, execution rate / F@0.05; details in `docs/REPORT.md`)

Qwen3.5-9B zero-shot 0% → LoRA on 5k Blender samples 75–82% → + execution-feedback DPO 93.9–96.2% → 4-dialect mixes: 25.6k 67%, 102k 84.9%, 270k (all 3DCodeVerse) 90.1%; 4 H100s: LoRA ≈17k tok/s, 270k samples / 293M tokens in 4.8 h.
