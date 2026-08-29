#!/bin/bash
# The 9B result to test at scale: adding reference images improved even the TEXT-only path (90.6%). Does that
# still hold for a 27B, or is it a small-model effect? Single view, because four views do not fit a 27B cutoff.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
V=27b_mm_img_text; RUN=runs/lf_qwen38_27b_mm_img_text
G=""; for _ in $(seq 1 60); do G=$(scripts/free_gpus.sh 60000); [ -n "$G" ] && break; sleep 60; done
[ -z "$G" ] && { echo "[$V] skipped: no GPU with 60 GB free after 60 min"; exit 0; }
echo "[$V] $(date) train on GPUs $G"
GPUS=$G scripts/lf_train.sh configs/lf/lora_27b_mm_img_text.yaml > logs/train_$V.log 2>&1
if [ ! -f $RUN/train_results.json ]; then
  # a 27B holding full weights per rank plus image activations may simply not fit; that is a result, so report the
  # real error rather than a bare "failed"
  echo "[$V] TRAIN FAILED: $(grep -iE 'out of memory|Error|Traceback' logs/train_$V.log | grep -v errors | tail -2 | tr '\n' ' ' | cut -c1-220)"; exit 0; fi
echo "[$V] train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-130)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$V.yaml
scripts/lf_export.sh configs/lf/export_$V.yaml > logs/export_$V.log 2>&1 || { echo "[$V] EXPORT FAILED"; exit 0; }
G1=""; for _ in $(seq 1 40); do G1=$(scripts/free_gpus.sh 70000 | cut -d, -f1); [ -n "$G1" ] && break; sleep 30; done
[ -z "$G1" ] && { echo "[$V] eval skipped: a 27B needs ~55 GB of weights and no card had 70 GB free"; exit 0; }
echo "[$V] $(date) eval on GPU $G1"
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q27b_mm_img_text_all --suites bench 2>&1 | tail -20
echo "[$V] DONE"
