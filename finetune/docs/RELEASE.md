# Released weights

Two LoRA adapters trained with this component are public on the Hugging Face Hub, grouped with the paper and
dataset in the [3DCodeVerse collection](https://huggingface.co/collections/ilabai/3dcodeverse-6ae554e2cf043729e3074b94).

| repo | base model | training run | size |
|---|---|---|---|
| [`ilabai/3dcodeverse-qwen3.6-27b-lora`](https://huggingface.co/ilabai/3dcodeverse-qwen3.6-27b-lora) | [Qwen/Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) | `qwen3.6-27b_lora_3dcv_text_A`: `3dcv_text_all` mix, 5,515 steps, global batch 48, eval loss 0.252 | 467 MB |
| [`ilabai/3dcodeverse-qwen3.8-27b-lora`](https://huggingface.co/ilabai/3dcodeverse-qwen3.8-27b-lora) | [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | `qwen3.8-27b_lora_3dcv_text_A`: 4,322 steps, final train loss ≈ 0.25 | 467 MB |

Both adapters use LoRA rank 16, alpha 32, dropout 0, on every attention, linear-attention
(`in_proj_qkv`, `in_proj_a/b/z`, `out_proj`), and MLP projection; peak LR 1e-4, cosine schedule, 3% warmup,
1 epoch, unmodified LLaMA-Factory with PEFT 0.18.1. Only the adapters are published. The base models load
from Qwen's repositories.

The weights are released under CC BY-NC-SA 4.0, matching the
[`ilabai/3dcodeverse`](https://huggingface.co/datasets/ilabai/3dcodeverse) data license. The base models are
Apache 2.0.

## Which runs were released

The working repository `williamium/3dcodeverse` holds six adapters. One adapter per base model was chosen.
The choice used training logs only; no execution-based scores existed for these runs:

| run | data | steps | loss | released |
|---|---|---|---|---|
| `qwen3.6-27b_lora_3dcv_text_A` | `3dcv_text_all` (full mix) | 5,515 | eval 0.2521 | yes |
| `qwen3.6-27b_lora_3dcv_text_B` | `3dcv_text_B` (subset) | 2,389 | eval 0.3097 | no |
| `qwen3.6-27b_lora_3dcv_text_C` | `3dcv_text_run1` (smaller subset) | 624 | eval 0.3068 | no |
| `qwen3.8-27b_lora_3dcv_text_A` | full mix | 4,322 | train ≈ 0.248 (last 50 logs) | yes |
| `qwen3.8-27b_lora_3dcv_text_A_v3` | same data as A | 4,322 | train ≈ 0.272 (last 50 logs) | no |
| `qwen3.8-27b_lora_3dcv_text_B` | subset B | 2,492 | — | no |

Eval losses of different data subsets are measured on different held-out sets and are not directly comparable.
`A` was chosen because it saw the most distinct programs, which is the main lever in [REPORT.md](REPORT.md)
§2.1. Run [`../../eval/llm/`](../../eval/llm/) before stating benchmark numbers for these adapters.

## Using them

Load with PEFT on top of the base checkpoint (`Qwen3_5ForConditionalGeneration`; the adapter only touches
`model.language_model`):

```python
from transformers import AutoModelForImageTextToText
from peft import PeftModel
base = AutoModelForImageTextToText.from_pretrained("Qwen/Qwen3.6-27B", torch_dtype="bfloat16", device_map="auto")
model = PeftModel.from_pretrained(base, "ilabai/3dcodeverse-qwen3.6-27b-lora")
```

For evaluation or serving, merge first. `configs/lf/export_lora_template.yaml` works with Hub ids in place of
local paths (`model_name_or_path: Qwen/Qwen3.6-27B`, `adapter_name_or_path: ilabai/3dcodeverse-qwen3.6-27b-lora`);
then evaluate the merged directory with `python -m llm.run_eval --backend vllm --model <merged>` from `eval/`.
Lower vLLM `max_num_seqs` if the engine fails to allocate Mamba cache blocks (see REPORT.md §6).

Prompts follow the training records: a dialect-specific system prompt plus a one-turn description, answered by
one fenced code block, with thinking disabled. The model cards on the Hub include a full example.

## Re-publishing

The card text, loss plots, and the upload were produced from a local staging folder; the logo files are
`../../assets/wordmark.png` and `../../assets/logo.png`. To update a card, edit `README.md` in the Hub repo
directly. The adapter weights should not change after release.
