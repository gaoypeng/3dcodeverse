#!/bin/bash
# dpo_exec_v2 generates NO runaway pairs of its own (3,247 of 3,687 greedy generations terminate cleanly) -- its
# execution-feedback DPO on Blender prompts already taught termination, which is very likely why it is the best
# model. So it cannot bootstrap its own stop-DPO. Instead transfer the pairs mmmix2 produced: the lesson they
# carry ("stop here, not there") is not specific to the model that generated them. If bench holds at ~96% while
# OpenSCAD/GLSL climb, that combination is the best model in the project.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
BASE=runs/lf_qwen35_9b_dpo_exec_v2/merged; RUN=runs/lf_qwen35_9b_best_stop
[ -d "$BASE" ] || { echo "[transfer] best model not exported"; exit 0; }
G=""; for _ in $(seq 1 90); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 60; done
[ -z "$G" ] && { echo "[transfer] no GPU with 40 GB free"; exit 0; }
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" \
    -e "s|^output_dir:.*|output_dir: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_best_stop|" configs/lf/dpo_stop.yaml > configs/lf/dpo_best_stop.yaml
echo "[transfer] $(date) train on GPU $G"
GPUS=$G scripts/lf_train.sh configs/lf/dpo_best_stop.yaml > logs/train_best_stop.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[transfer] TRAIN FAILED: $(grep -iE 'out of memory|Error' logs/train_best_stop.log | grep -v errors | tail -1 | cut -c1-180)"; exit 0; }
echo "[transfer] train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-130)"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_best_stop.yaml
scripts/lf_export.sh configs/lf/export_best_stop.yaml > logs/export_best_stop.log 2>&1 || { echo "[transfer] EXPORT FAILED"; exit 0; }
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_best_stop_greedy 2>&1 | tail -9
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_best_stop_t07 --temp 0.7 --seed 1 2>&1 | tail -9
echo "[transfer] DONE"
