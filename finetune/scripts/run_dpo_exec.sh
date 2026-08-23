#!/bin/bash
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft; GPU=${GPUS:-2,3}; NAME=dpo_exec_v1; RUN=runs/lf_qwen35_9b_$NAME
echo "[dpo] $(date) export v1 merged"; [ -d runs/lf_qwen35_9b_lora_v1/merged ] || scripts/lf_export.sh configs/lf/export_v1.yaml > logs/export_v1_dpo.log 2>&1 || { echo "[dpo] EXPORT FAILED"; exit 1; }
echo "[dpo] $(date) train on GPUs $GPU"; GPUS=$GPU scripts/lf_train.sh configs/lf/$NAME.yaml > logs/train_$NAME.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[dpo] TRAIN FAILED"; grep -E "Error|error" logs/train_$NAME.log | tail -5 | cut -c1-200; exit 1; }
echo "[dpo] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_v1/merged|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$NAME.yaml
scripts/lf_export.sh configs/lf/export_$NAME.yaml > logs/export_$NAME.log 2>&1 || { echo "[dpo] EXPORT2 FAILED"; exit 1; }
echo "[dpo] $(date) merged"; G1=$(echo $GPU | cut -d, -f1)
GPUS=$G1 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_$NAME --no_think --max_new_tokens 6144 > logs/eval_$NAME.log 2>&1
echo "[dpo] $(date) eval done"; cat eval/out/qwen35_9b_$NAME/summary.json; rm -rf $RUN/merged; echo "[dpo] DONE"
