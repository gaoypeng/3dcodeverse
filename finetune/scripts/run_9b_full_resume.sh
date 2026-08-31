#!/bin/bash
# Continue the 271k-sample 9B from checkpoint-4200 after dgx01 had to be given back. Four cards instead of seven,
# with accumulation raised 4 -> 7 so samples-per-step stays 28 and the resumed step count still means the same
# thing. Scored under both decodings, per section 13.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_full
G=${GPUS:-$(scripts/free_gpus.sh 60000)}
[ -z "$G" ] && { echo "[full9b] no cards with 60 GB free"; exit 1; }
echo "[full9b] $(date) resuming from checkpoint-4200 on GPUs $G"
GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_full_resume.yaml > logs/train_9b_full_resume.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[full9b] TRAIN FAILED: $(grep -iE 'out of memory|Error|Traceback' logs/train_9b_full_resume.log | grep -v errors | tail -1 | cut -c1-190)"; exit 1; }
echo "[full9b] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-150)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_full.yaml
scripts/lf_export.sh configs/lf/export_9b_full.yaml > logs/export_9b_full.log 2>&1 || { echo "[full9b] EXPORT FAILED"; exit 1; }
G1=$(echo $G | cut -d, -f1)
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_full_greedy 2>&1 | tail -9
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_full_t07 --temp 0.7 --seed 1 2>&1 | tail -9
echo "[full9b] ALL DONE"
