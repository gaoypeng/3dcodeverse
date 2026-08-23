#!/bin/bash
# like run_exp.sh but merges onto Qwen3.5-4B
set -uo pipefail; GPU=$1; NAME=$2; cd /wekafs/ict/hx_624/llm-ft
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_$NAME; OUT=/wekafs/ict/hx_624/llm-ft/eval/out/qwen35_9b_lora_$NAME
echo "[exp $NAME] $(date) train on GPU(s) $GPU"
GPUS=$GPU scripts/lf_train.sh configs/lf/lora_$NAME.yaml > logs/train_$NAME.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[exp $NAME] TRAIN FAILED"; exit 1; }
echo "[exp $NAME] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-4B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $RUN|" -e "s|export_dir:.*|export_dir: $RUN/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$NAME.yaml
scripts/lf_export.sh configs/lf/export_$NAME.yaml > logs/export_$NAME.log 2>&1 || { echo "[exp $NAME] EXPORT FAILED"; exit 1; }
echo "[exp $NAME] $(date) merged"
GPUS=$GPU eval/run_eval_vllm.sh $RUN/merged $OUT --no_think --max_new_tokens 6144 > logs/eval_$NAME.log 2>&1
echo "[exp $NAME] $(date) eval done"; cat $OUT/summary.json; rm -rf $RUN/merged; echo "[exp $NAME] DONE"
