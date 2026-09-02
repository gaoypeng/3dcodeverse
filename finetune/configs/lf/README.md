# configs/lf/

Named `<stage>_<model>_<tuning>_lr<lr>_len<ctx>_<data>.yaml`, so the filename carries the settings that vary.
Everything else (batch 1, bf16, cosine schedule, 1 epoch for SFT) is the same across runs.

| config | data |
|---|---|
| `sft_9b_lora64_lr1e-4_len8k_mmmix-plus.yaml` | current best SFT line: mm image+text pairs + `md_9b_plus` |
| `sft_9b_lora64_lr1e-4_len8k_mmmix-noleak.yaml` | same recipe on the leak-free mix (source of the transferable stop pairs) |
| `sft_9b_lora64_lr1e-4_len8k_full9b.yaml` | the full corpus |
| `sft_9b_lora64_lr1e-4_len8k_pubimg.yaml` | the published image pairs (169k rows), local `media_dir` |
| `sft_9b_lora64_lr1e-4_len8k_pairsmix.yaml` | published image+text and text pairs |
| `sft_9b_lora64_lr1e-4_len8k_hub-remote.yaml` | text-only, streamed with `dataset_dir: REMOTE:ilabai/3dcodeverse` |
| `dpo_9b_lora16_lr5e-6_len2k_stop-r1.yaml` | stop-DPO round 1 (also the template `stop_dpo_round.sh` seds) |
| `dpo_9b_lora16_lr5e-6_len2k_stop-r2.yaml` | round 2, from the round-1 model |

`export_lora_template.yaml` is the merge template used by `scripts/export_lora.sh`; `ds_z3_offload.json` is the
ZeRO-3 config (LoRA runs are ~7.8x slower under it than DDP — use it only when the model does not fit).
Superseded configs are in `archive/configs/`.
