#!/bin/bash
# Control for the md_max regression: same sources & token budget, but 1 caption per sample + all bootstrapped Blender.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
until grep -qE "\[27bv2\] (ALL DONE|TRAIN FAILED|EXPORT FAILED)" logs/run_27b_v2.log 2>/dev/null; do sleep 120; done
IDLE=0; while [ $IDLE -lt 3 ]; do
  if [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1,2,3 | sort -n | tail -1)" -lt 8000 ]; then IDLE=$((IDLE+1)); else IDLE=0; fi; sleep 30; done
echo "[v3] $(date) train 9B on md_v3 (1 caption/sample + bootstrapped blender)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_md_v3.yaml > logs/train_md_v3.log 2>&1
RUN=runs/lf_qwen35_9b_lora_md_v3
[ -f $RUN/train_results.json ] || { echo "[v3] TRAIN FAILED"; exit 1; }
echo "[v3] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_md_v3.yaml
scripts/lf_export.sh configs/lf/export_md_v3.yaml > logs/export_md_v3.log 2>&1 || { echo "[v3] EXPORT FAILED"; exit 1; }
GPUS=0 TP=1 eval/eval_all_run2.sh $RUN/merged q9b_v3 --max_new 8192 2>&1 | grep -E "gen-multi|status counts|dialect|DONE" | tail -20
GPUS=1 TP=1 eval/eval_all_run2.sh $RUN/merged q9b_v3_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "dialect|DONE" | tail -4
echo "[v3] ALL DONE"
