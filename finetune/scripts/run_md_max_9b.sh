#!/bin/bash
# After the 27B pipeline: 9B on the caption-augmented full corpus at the SAME token budget as md_xl (~300M)
# -> answers "does using ALL sources + 3 captions/sample beat 270k single-caption pairs at equal compute?"
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
until grep -qE "\[27b\] (ALL DONE|TRAIN FAILED|SMOKE FAILED|EXPORT FAILED)" logs/run_27b.log 2>/dev/null; do sleep 120; done
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1,2,3 | sort -n | tail -1)" -lt 8000 ]; do sleep 60; done
echo "[9bmax] $(date) train 9B on md_max_9b (364k pairs / ~304M tok, 6 dialects, 3 captions/sample)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/lora_md_max_9b.yaml > logs/train_md_max_9b.log 2>&1
RUN=runs/lf_qwen35_9b_lora_md_max
[ -f $RUN/train_results.json ] || { echo "[9bmax] TRAIN FAILED"; exit 1; }
echo "[9bmax] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_md_max_9b.yaml
scripts/lf_export.sh configs/lf/export_md_max_9b.yaml > logs/export_md_max_9b.log 2>&1 || { echo "[9bmax] EXPORT FAILED"; exit 1; }
GPUS=0 TP=1 eval/eval_all_run2.sh $RUN/merged q9b_md_max --max_new 8192 2>&1 | grep -E "gen-multi|extract\]|status counts|dialect|DONE" | tail -30
echo "[9bmax] ALL DONE"
