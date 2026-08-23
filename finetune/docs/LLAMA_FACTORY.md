# How this project uses LLaMA-Factory (primer)

[LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) is a config-driven finetuning framework on top of 🤗 transformers/peft/trl/deepspeed: you describe *model + data + stage + method + trainer args* in one YAML and run `llamafactory-cli train that.yaml`. We use it **unmodified** (editable install of upstream commit `c4e09c7`, 0.9.6.dev0; `git status` clean). Everything project-specific is a YAML in `configs/lf/` or a dataset entry in `data_specs/dataset_info.json`.

## The pieces we touch
| LLaMA-Factory concept | where in this repo | notes |
|---|---|---|
| **dataset registry** `dataset_info.json` | `data_specs/dataset_info.json` (copy to your `dataset_dir`) | every SFT set is ShareGPT JSON: `{"conversations":[{"from":"human","value":prompt},{"from":"gpt","value":code}],"system":...}`; DPO sets add `"chosen"/"rejected"` and `"ranking": true`. `scripts/to_llamafactory.py` writes both the JSON and the registry entry. |
| **template** | `template: qwen3_5`, `enable_thinking: false` | Qwen3.5 is a VLM wrapper (`Qwen3_5ForConditionalGeneration`); we freeze the vision tower (`freeze_vision_tower: true`, `freeze_multi_modal_projector: true`). The template treats literal `<image>/<video>/<audio>` in text as media placeholders → escape them in data (`scripts/build_md_xl.py` does). |
| **stage** | `stage: sft` (`lora_*.yaml`, `qwen35_9b_full_sft*.yaml`, `full_md_bal.yaml`), `stage: dpo` (`dpo_*.yaml`) | DPO: `pref_beta: 0.1`, `pref_loss: sigmoid`, lr 5e-6, cutoff 4096 (pairs are ~2× the memory of SFT). |
| **method** | `finetuning_type: lora` (r16/r64, alpha 2r, dropout 0.05, `lora_target: all`) or `full` | full FT uses `deepspeed: …/ds_z3_config.json` (ZeRO-3; `configs/lf/ds_z3_offload.json` for 2-GPU CPU-offload). |
| **packing** | `packing: true`, `neat_packing: true`, `cutoff_len: 8192` | neat_packing requires `per_device_eval_batch_size: 1`; keep `save_steps ≤ eval_steps`. |
| **attention** | `flash_attn: fa2` | plus fla/causal-conv1d for the Gated-DeltaNet layers (triton 3.7.1 on Hopper). |
| **tokenized cache** | `tokenized_path: …/tokenized_<name>_len8192_packed` | second run on the same data skips preprocessing. |
| **launch** | `scripts/lf_train.sh <yaml> key=value…` | = `torchrun --nproc_per_node=$N llamafactory-cli train <abs yaml> key=value` with `CUDA_VISIBLE_DEVICES`, per-run Triton cache; any YAML key can be overridden on the CLI (we override `gradient_accumulation_steps`, `cutoff_len`, `num_train_epochs`). |
| **merge LoRA** | `scripts/lf_export.sh configs/lf/export_<name>.yaml` | `llamafactory-cli export` → a plain HF checkpoint for vLLM. |
| **checkpoints** | `save_steps`, `save_total_limit`, `save_only_model: true` | checkpoint sweeps (`scripts/*_ckpt_sweep.sh`) merge each `checkpoint-N` and evaluate it. |

## Minimal recipe (what `configs/lf/lora_md_xl.yaml` says, annotated)
```yaml
model_name_or_path: <path to Qwen3.5-9B>
trust_remote_code: true
flash_attn: fa2
stage: sft
do_train: true
finetuning_type: lora          # or: full  (+ deepspeed: ds_z3_config.json)
lora_rank: 64
lora_alpha: 128
lora_dropout: 0.05
lora_target: all
freeze_vision_tower: true
freeze_multi_modal_projector: true
dataset_dir: <dir with dataset_info.json>
dataset: md_xl_train
eval_dataset: md_xl_val
template: qwen3_5
enable_thinking: false
cutoff_len: 8192
packing: true
neat_packing: true
tokenized_path: <cache dir>
output_dir: <run dir>
per_device_train_batch_size: 1
gradient_accumulation_steps: 2   # 4 GPUs × 1 × 2 × 8192 ≈ 64k tokens / step
learning_rate: 1.0e-4
num_train_epochs: 1.0
lr_scheduler_type: cosine
warmup_ratio: 0.03
bf16: true
gradient_checkpointing: true
per_device_eval_batch_size: 1
eval_strategy: steps
eval_steps: 450
save_steps: 450
save_total_limit: 14
logging_steps: 5
report_to: tensorboard
```
Throughput on 4×H100 with this recipe: ≈17k tokens/s (LoRA r64), 270k samples / 293M tokens in 4.8 h; full-parameter ZeRO-3 on 4 GPUs ≈14k tokens/s.

## Pitfalls we hit (details in `docs/REPORT.md` §1)
1. torch 2.9.x + Qwen3.5 (Conv3D vision tower) is rejected by LLaMA-Factory → torch 2.8.0.
2. fla Gated-DeltaNet backward on Hopper needs triton 3.7.1.
3. `neat_packing` + eval batch size > 1 crashes at the first eval.
4. `<image>` as literal text in a sample → "number of images does not match" → escape in data.
5. Triton cache on a network FS races between concurrent runs → per-run local cache.
6. DPO with 8k cutoff OOMs on long GLSL/OpenSCAD pairs on 80 GB → cutoff 4096.
7. All paths handed to LLaMA-Factory must be absolute (it chdirs).
