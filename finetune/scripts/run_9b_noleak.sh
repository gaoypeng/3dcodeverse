#!/bin/bash
# Leakage probe: identical to the md_9b_clean run except the 164 rows whose source is a 3DCodeBench factory.
# Measures what that 0.09% of contamination is actually worth on the benchmark.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
G=$(scripts/free_gpus.sh)
echo "[noleak] $(date) train on GPUs $G"
GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_noleak.yaml > logs/train_9b_noleak.log 2>&1
RUN=runs/lf_qwen35_9b_lora_noleak
[ -f $RUN/train_results.json ] || { echo "[noleak] TRAIN FAILED: $(grep -iE 'out of memory|error' logs/train_9b_noleak.log | grep -v errors | tail -1 | cut -c1-160)"; exit 1; }
echo "[noleak] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-140)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_noleak.yaml
scripts/lf_export.sh configs/lf/export_9b_noleak.yaml > logs/export_9b_noleak.log 2>&1 || { echo "[noleak] EXPORT FAILED"; exit 1; }
GPUS=0 TP=1 eval/eval_all_run2.sh $RUN/merged q9b_noleak --max_new 8192 --suites bench blender cadquery 2>&1 | grep -E "status counts|dialect|DONE" | tail -8
echo "[noleak] ALL DONE"
