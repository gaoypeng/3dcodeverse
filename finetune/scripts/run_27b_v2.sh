#!/bin/bash
# 27B six-dialect LoRA, DDP @ cutoff 4096 (ZeRO-3 was 7.8x slower), then merge + full eval
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
echo "[27b] $(date) LoRA DDP train (md_27b_max4k, ~64M tok, 4 GPUs, ~5 h)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_27b_ddp.yaml > logs/train_27b_ddp.log 2>&1
RUN=runs/lf_qwen38_27b_lora_md_max
[ -f $RUN/train_results.json ] || { echo "[27b] TRAIN FAILED: $(grep -E 'OutOfMemory|Error' logs/train_27b_ddp.log | grep -v errors | tail -1 | cut -c1-200)"; exit 1; }
echo "[27b] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" \
    -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" \
    -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_max.yaml
scripts/lf_export.sh configs/lf/export_27b_max.yaml > logs/export_27b_max.log 2>&1 || { echo "[27b] EXPORT FAILED"; exit 1; }
echo "[27b] $(date) eval tuned 27B (TP=2)"
GPUS=0,1 TP=2 eval/eval_all_run2.sh $RUN/merged q27b_ft --max_new 8192 2>&1 | grep -E "gen-multi|extract\]|status counts|dialect|DONE" | tail -30
echo "[27b] ALL DONE"
