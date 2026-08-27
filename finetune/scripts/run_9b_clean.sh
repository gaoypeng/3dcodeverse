#!/bin/bash
# 9B on the cleaned corpus: compile-verified GLSL (flattened, Common merged), recovered URDF (7th dialect),
# deduped per-subdir data, one caption per sample, plus the execution-verified bootstrapped Blender set.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
G=$(scripts/free_gpus.sh); N=$(echo "$G" | awk -F, '{print NF}')
echo "[9bclean] $(date) train on GPUs $G (191.9k pairs / 177M tok, 7 dialects)"
GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_clean.yaml > logs/train_9b_clean.log 2>&1
RUN=runs/lf_qwen35_9b_lora_clean
[ -f $RUN/train_results.json ] || { echo "[9bclean] TRAIN FAILED: $(grep -E 'out of memory|Error' logs/train_9b_clean.log | grep -v errors | tail -1 | cut -c1-160)"; exit 1; }
echo "[9bclean] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_clean.yaml
scripts/lf_export.sh configs/lf/export_9b_clean.yaml > logs/export_9b_clean.log 2>&1 || { echo "[9bclean] EXPORT FAILED"; exit 1; }
GPUS=0 TP=1 eval/eval_all_run2.sh $RUN/merged q9b_clean --max_new 8192 2>&1 | grep -E "gen-multi|status counts|dialect|DONE" | tail -20
GPUS=1 TP=1 eval/eval_all_run2.sh $RUN/merged q9b_clean_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "dialect|DONE" | tail -4
echo "[9bclean] ALL DONE"
