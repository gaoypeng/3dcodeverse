# How this project uses LLaMA-Factory

[LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) consumes a YAML describing the model,
data, training stage, and trainer arguments via `llamafactory-cli train <config.yaml>`. This project
uses upstream unmodified: the training environment snapshot pins commit
`c4e09c7cbe18844816af9e18a97fe465515edbcd` (0.9.6.dev0) in
[`../env/requirements-lf.txt`](../env/requirements-lf.txt).

Our [`../llamafactory/`](../llamafactory/) directory contains **training-data tools**, not framework
source: conversion, validation, multimodal pair construction, token counting, mix sampling, and Hub
packaging. Recipes remain in `configs/lf/`, environment snapshots in `env/`, and train/export
launchers in `scripts/` (paths below are relative to `finetune/`).

## The integration points

| concept | where | notes |
|---|---|---|
| dataset registry | each recipe's `dataset_dir` / `dataset_info.json` | `data_specs/dataset_info.json` is a historical snapshot; current local multimodal recipes require additional external registry entries and data |
| data conversion | `llamafactory/to_llamafactory.py` and the other builders | ShareGPT uses `conversations` and `system`; preference sets add `chosen` / `rejected` and registry `ranking: true` |
| current recipes | `configs/lf/sft_*.yaml`, `configs/lf/dpo_*.yaml` | six SFT recipes and two stop-DPO recipes; see [`../configs/lf/README.md`](../configs/lf/README.md) |
| template | `template: qwen3_5`, `enable_thinking: false` | Qwen3.5 is a VLM wrapper; current recipes freeze the vision tower and multimodal projector |
| LoRA | `finetuning_type: lora` | current SFT uses rank 64 / alpha 128; stop-DPO uses rank 16 / alpha 32 |
| stop-DPO | `stage: dpo` | current recipes use `pref_beta: 0.1`, sigmoid loss, learning rate 5e-6, and cutoff 2048; historical execution/geometry-feedback studies used other recipes |
| multimodal packing | `packing: false`, `neat_packing: false` | current SFT recipes disable packing for image-containing data |
| attention | `flash_attn: fa2` | original Qwen3.5/Hopper environment also uses fla, causal-conv1d, and triton 3.7.1 |
| preprocessing cache | `tokenized_path` in each local SFT recipe | reuse only for the same data and preprocessing settings |
| training | `scripts/lf_train.sh <yaml> key=value…` | launches the installed LF CLI with selected GPUs and a local Triton cache; the wrapper still contains workstation paths |
| merging | `scripts/export_lora.sh <run_dir> <base_model>` | builds an export YAML from `configs/lf/export_lora_template.yaml` and calls `scripts/lf_export.sh`; specify the same base model used for the adapter |
| evaluation | `../eval/llm/` | model generation, dialect execution, geometry metrics, and reporting; see its README for the current CLI |

Full-parameter and ZeRO-3 experiments are described in the historical reports. DeepSpeed templates
remain in `configs/`; they do not turn the current LoRA recipes into full-parameter recipes.

## Current SFT example

The complete current recipe is
[`sft_9b_lora64_lr1e-4_len8k_mmmix-plus.yaml`](../configs/lf/sft_9b_lora64_lr1e-4_len8k_mmmix-plus.yaml).
Its data and training settings include:

```yaml
stage: sft
finetuning_type: lora
lora_rank: 64
lora_alpha: 128
lora_target: all
dataset_dir: /path/to/mm_mix
dataset: 3dcv_mm_img_text_train,3dcv_mm_img_train,md_9b_plus_train
eval_dataset: 3dcv_mm_img_text_val
template: qwen3_5
enable_thinking: false
cutoff_len: 8192
packing: false
neat_packing: false
per_device_train_batch_size: 1
gradient_accumulation_steps: 6
learning_rate: 0.0001
num_train_epochs: 1.0
bf16: true
```

This is an excerpt, not a standalone config. Prepare the named datasets and their registry, and
adapt the model, cache, data, and output paths in the complete file before launching it. From
`finetune/`, after adapting the workstation launchers:

```bash
GPUS=0,1,2,3 bash scripts/lf_train.sh configs/lf/sft_9b_lora64_lr1e-4_len8k_mmmix-plus.yaml
bash scripts/export_lora.sh /absolute/path/to/run /absolute/path/to/the-same-base-model
```

The wrapper forwards LF CLI overrides, but it currently derives its own log and cache paths from
the original YAML before applying those overrides. Set the intended output path in the recipe when
using these wrappers. Changing `env.sh` alone does not relocate their hardcoded conda/LF paths.

## Data and environment checks

- Every `dataset` and `eval_dataset` name must resolve in the registry selected by `dataset_dir`.
  Do not assume the historical registry snapshot contains a current multimodal mix.
- Literal `<image>` tokens in code count as media placeholders too. Run
  `python llamafactory/audit_pairs.py ...` against the prepared pair datasets before publishing;
  the pair builder rejects assistant code containing that literal token.
- Image placeholders must match the image list, media paths must resolve, and image-containing
  rows must use the multimodal settings above.
- The original training environment uses torch 2.8.0 and triton 3.7.1 for its Qwen3.5/Hopper stack;
  see [`SETUP.md`](SETUP.md) and the pinned environment snapshot for the remaining dependencies.
- Keep Triton caches on local disk for concurrent training on the original network filesystem.
- The stop-DPO shell pipeline still expects the historical generation script and output format.
  The current `eval/llm/` API uses different filenames/fields, so migrating that pipeline requires a
  schema adapter and validation of its pairing behavior.

Historical runs and their pitfalls remain in [`REPORT.md`](REPORT.md)
and [`../llm_finetune_exps.md`](../llm_finetune_exps.md). Their old paths and recipe names are records
of those runs, rather than the current entrypoint inventory.
