# finetune/ — training data and LLaMA-Factory recipes for 3DCodeVerse

This component prepares training data and fine-tunes open models to generate Blender-Python,
CadQuery, OpenSCAD, GLSL, and three.js code. The harness in [`../harness/`](../harness/) generates
data with API models; standalone model evaluation lives in [`../eval/llm/`](../eval/llm/).

Training uses **unmodified upstream LLaMA-Factory**, pinned in `env/requirements-lf.txt`, through
`llamafactory-cli` and ordinary YAML recipes. `llamafactory/` contains this project's data-preparation
tools; it is not a copy or fork of the LLaMA-Factory framework.

## Layout

| path | what |
|---|---|
| [`llamafactory/`](llamafactory/) | canonical training-data scripts: ShareGPT conversion, quality checks, multimodal pairs, token counts, mix sampling, and Hub packaging; includes the per-source `runners/` |
| [`scripts/`](scripts/) | training/export launchers and workstation orchestration; six old Python data-script paths are compatibility forwarders to `llamafactory/` |
| [`configs/lf/`](configs/lf/) | current SFT and stop-DPO YAML recipes, the LoRA export template, and a DeepSpeed offload template; see its README for the current recipe list |
| `data_specs/` | recorded dataset registry and data statistics; each run must also have the registry and datasets named by its own `dataset_dir` |
| `env/requirements-{llmft,lf,vllm}.txt` | snapshots of the original data/eval, LLaMA-Factory training, and vLLM inference environments |
| `env.sh` | original workstation shell setup for environments, caches, and a local secrets file |
| [`docs/REPORT.md`](docs/REPORT.md) | experiment conclusions; `llm_finetune_exps.md` is the per-run log |

Configs, environments, and training launchers keep their existing locations. General corpus curation
and the contributor CLI remain in [`../toolkits/`](../toolkits/).

## Data preparation

Use the tools in [`llamafactory/`](llamafactory/README.md) for corpus conversion, validation,
multimodal datasets, and mix sampling. For example, from this directory, after preparing the source
`train.jsonl` and `val.jsonl`:

```bash
python llamafactory/sample_mix.py --src /path/to/md_max --out /path/to/mix \
  --budget "blender=all,cadquery=25M,glsl=25M" --captions_per_sample 1
```

The study's ShareGPT data is published as
**[`3dcodeverse/3dcodeverse-2m`](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-2m/tree/main/finetune)** under `finetune/`
(see the dataset card for its non-commercial terms; formerly the private repo `3dcodeverse/3dcodeverse-llamafactory`). `llamafactory/build_hf_dataset.py`
packages the corpus, historical preference sets, held-out tests, and mix descriptions from prepared
local inputs. The published pair datasets are also used by the Hub recipe in `configs/lf/`.

`data_specs/dataset_info.json` is a historical registry snapshot. The current local SFT recipes point
at separate `mm_mix`, `full9b`, `pairs_mix`, or `pub_img` directories on the original workstation;
their complete registries and data are not included in this checkout. Prepare or obtain the matching
data and registry before launching a recipe. The remote recipe reads its registry from the Hub and
still needs access to the dataset and its local media files.

## Train, export, evaluate

Read [`docs/SETUP.md`](docs/SETUP.md) for the original environment setup and
[`docs/LLAMA_FACTORY.md`](docs/LLAMA_FACTORY.md) for the integration. The shell launchers and data
tools still contain `/wekafs/ict/hx_624/...` workstation paths. Adapt `env.sh`, the launchers, the
YAML model/data/output paths, and tool paths before running elsewhere; changing `env.sh` alone does
not update the other files.

After that setup and data preparation, a current SFT recipe can be launched from `finetune/`:

```bash
GPUS=0,1,2,3 bash scripts/lf_train.sh configs/lf/sft_9b_lora64_lr1e-4_len8k_mmmix-plus.yaml
bash scripts/export_lora.sh /absolute/path/to/run /absolute/path/to/the-same-base-model
```

The export script writes the merged model into `<run>/merged`. In the evaluation environment, from
the repository's `eval/` directory, use the current standalone evaluator:

```bash
python -m llm.run_eval --backend vllm --model /absolute/path/to/run/merged \
  --run my_sft --suites headline heldout
```

See [`../eval/llm/README.md`](../eval/llm/README.md) for tool/asset setup and suite decoding defaults.

The stop-DPO recipes remain in `configs/lf/dpo_*.yaml`. Their historical generation
and workstation orchestration scripts are no longer distributed. Prepare matching
preference data before using these training recipes.

## Released weights

LoRA adapters for Qwen3.6-27B and Qwen3.8-27B are public on the Hub:
[`3dcodeverse/3dcodeverse-qwen3.6-27b-lora`](https://huggingface.co/3dcodeverse/3dcodeverse-qwen3.6-27b-lora) and
[`3dcodeverse/3dcodeverse-qwen3.8-27b-lora`](https://huggingface.co/3dcodeverse/3dcodeverse-qwen3.8-27b-lora), collected with the
paper and dataset in the [3DCodeVerse collection](https://huggingface.co/collections/3dcodeverse/3dcodeverse).
[`docs/RELEASE.md`](docs/RELEASE.md) records which runs were chosen and why, and how to load, merge, and evaluate them.

## Experiment records

[`llm_finetune_exps.md`](llm_finetune_exps.md) and [`docs/REPORT.md`](docs/REPORT.md) document the
results and lessons from the study. Historical run names and commands in these records may refer to
recipes or drivers no longer present in the current checkout. Use [`configs/lf/README.md`](configs/lf/README.md)
for the current recipe inventory.
