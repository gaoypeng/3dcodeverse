#!/bin/bash
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_noleak; G=$(scripts/free_gpus.sh)
[ -f $RUN/merged/config.json ] || {
  sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_noleak.yaml
  scripts/lf_export.sh configs/lf/export_9b_noleak.yaml > logs/export_9b_noleak.log 2>&1 || { echo "[noleak] EXPORT FAILED"; exit 1; }; }
echo "[noleak] $(date) eval (leakage probe: same mix minus the 164 benchmark-factory rows)"
GPUS=$(echo $G|cut -d, -f1) TP=1 eval/eval_all_run2.sh $RUN/merged q9b_noleak --max_new 8192 --suites bench blender cadquery 2>&1 | grep -E "status counts|dialect|DONE" | tail -6
echo "[noleak] ALL DONE"
