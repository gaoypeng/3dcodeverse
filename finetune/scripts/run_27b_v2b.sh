#!/bin/bash
# After the 9B md_max run: 27B v2 — Blender-heavy mix (adds the 15.7k execution-verified bootstrapped samples,
# Blender x2) to fix the 3DCodeBench regression; then merge + eval (greedy everywhere + T=0.7 for openscad/glsl).
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
# require the GPUs to stay idle for 3 consecutive checks (an eval between suites can look idle for a moment)
IDLE=0
while [ $IDLE -lt 3 ]; do
  if [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1,2,3 | sort -n | tail -1)" -lt 8000 ]; then IDLE=$((IDLE+1)); else IDLE=0; fi
  sleep 30
done
echo "[27bv2] $(date) train (104k pairs / 77M tok, blender-heavy, DDP@4096, 4 GPUs)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_27b_v2.yaml > logs/train_27b_v2.log 2>&1
RUN=runs/lf_qwen38_27b_lora_v2
[ -f $RUN/train_results.json ] || { echo "[27bv2] TRAIN FAILED: $(grep -E 'OutOfMemory|Error' logs/train_27b_v2.log | grep -v errors | tail -1 | cut -c1-200)"; exit 1; }
echo "[27bv2] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_v2.yaml
scripts/lf_export.sh configs/lf/export_27b_v2.yaml > logs/export_27b_v2.log 2>&1 || { echo "[27bv2] EXPORT FAILED"; exit 1; }
echo "[27bv2] $(date) eval greedy (TP=2)"
GPUS=0,1 TP=2 eval/eval_all_run2.sh $RUN/merged q27b_v2 --max_new 8192 2>&1 | grep -E "gen-multi|extract\]|status counts|dialect|DONE" | tail -30
echo "[27bv2] $(date) eval sampled (T=0.7) for the boilerplate-heavy dialects"
GPUS=0,1 TP=2 eval/eval_all_run2.sh $RUN/merged q27b_v2_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "gen-multi|dialect|DONE" | tail -8
echo "[27bv2] ALL DONE"
