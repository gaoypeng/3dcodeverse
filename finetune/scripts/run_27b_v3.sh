#!/bin/bash
# 27B v3: keep v2's Blender strength but restore CadQuery volume (45M tok) — can one model top both 3DCodeBench and CadQuery?
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
GPUS_USE=$(scripts/free_gpus.sh); NGPU=$(echo "$GPUS_USE" | awk -F, '{print NF}')
echo "[27bv3] using GPUs $GPUS_USE ($NGPU cards)"
echo "[27bv3] $(date) train"
GPUS=$GPUS_USE scripts/lf_train.sh configs/lf/lora_27b_v3.yaml > logs/train_27b_v3.log 2>&1
RUN=runs/lf_qwen38_27b_lora_v3
[ -f $RUN/train_results.json ] || { echo "[27bv3] TRAIN FAILED"; exit 1; }
echo "[27bv3] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_v3.yaml
scripts/lf_export.sh configs/lf/export_27b_v3.yaml > logs/export_27b_v3.log 2>&1 || { echo "[27bv3] EXPORT FAILED"; exit 1; }
GPUS=$GPUS_USE TP=$NGPU eval/eval_all_run2.sh $RUN/merged q27b_v3 --max_new 8192 2>&1 | grep -E "gen-multi|status counts|dialect|DONE" | tail -20
GPUS=$GPUS_USE TP=$NGPU eval/eval_all_run2.sh $RUN/merged q27b_v3_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "dialect|DONE" | tail -4
echo "[27bv3] ALL DONE"
