#!/bin/bash
# 27B pipeline: wait for free GPUs -> ZeRO-3 LoRA smoke test -> full train -> merge -> eval_all (TP=2) on all six suites
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
echo "[27b] $(date) waiting for GPUs 0-3 to be free"
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1,2,3 | sort -n | tail -1)" -lt 8000 ]; do sleep 60; done   # all four idle
echo "[27b] $(date) ZeRO-3 smoke test (6 steps, 4 GPUs)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_27b_md_z3.yaml max_steps=6 save_steps=100000 eval_steps=100000 \
  output_dir=/wekafs/ict/hx_624/llm-ft/runs/_smoke_27b_z3 > logs/smoke_27b_z3.log 2>&1
if ! grep -q "train_runtime" logs/smoke_27b_z3.log; then
  echo "[27b] SMOKE FAILED: $(grep -E 'OutOfMemoryError|RuntimeError|Error' logs/smoke_27b_z3.log | grep -v errors | tail -1 | cut -c1-200)"; exit 1
fi
echo "[27b] smoke ok: $(grep -oE "'train_runtime': [0-9.]+" logs/smoke_27b_z3.log | tail -1)"; rm -rf runs/_smoke_27b_z3
echo "[27b] $(date) full LoRA train (md_27b_max: 78k pairs / 67.6M tok, 6 dialects)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_27b_md_z3.yaml > logs/train_27b_max.log 2>&1
RUN=runs/lf_qwen38_27b_lora_md_max
[ -f $RUN/train_results.json ] || { echo "[27b] TRAIN FAILED: $(grep -E 'Error|OutOfMemory' logs/train_27b_max.log | grep -v errors | tail -1 | cut -c1-200)"; exit 1; }
echo "[27b] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" \
    -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" \
    -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_max.yaml
scripts/lf_export.sh configs/lf/export_27b_max.yaml > logs/export_27b_max.log 2>&1 || { echo "[27b] EXPORT FAILED"; exit 1; }
echo "[27b] $(date) eval tuned model (TP=2, GPUs 0,1)"
GPUS=0,1 TP=2 eval/eval_all.sh $RUN/merged q27b_ft --max_new 8192 2>&1 | grep -E "gen-multi|extract\]|status counts|dialect|DONE" | tail -30
echo "[27b] ALL DONE"
