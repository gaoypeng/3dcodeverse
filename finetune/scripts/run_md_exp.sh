#!/bin/bash
# run_md_exp.sh <GPU(s)> <NAME>: LoRA train (configs/lf/lora_<NAME>.yaml) -> merge -> 3DCodeBench eval + 4-dialect eval -> rm merged
set -uo pipefail; GPU=$1; NAME=$2; NGPU=$(echo $GPU | tr ',' '\n' | wc -l); ACC=$((8 / NGPU)); cd /wekafs/ict/hx_624/llm-ft
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_$NAME
echo "[md $NAME] $(date) train on GPU(s) $GPU"
GPUS=$GPU scripts/lf_train.sh configs/lf/lora_$NAME.yaml gradient_accumulation_steps=$ACC > logs/train_$NAME.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[md $NAME] TRAIN FAILED"; exit 1; }
echo "[md $NAME] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|adapter_name_or_path:.*|adapter_name_or_path: $RUN|" -e "s|export_dir:.*|export_dir: $RUN/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$NAME.yaml
scripts/lf_export.sh configs/lf/export_$NAME.yaml > logs/export_$NAME.log 2>&1 || { echo "[md $NAME] EXPORT FAILED"; exit 1; }
G1=$(echo $GPU | cut -d, -f1)
GPUS=$G1 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_lora_$NAME --no_think --max_new_tokens 6144 > logs/eval_$NAME.log 2>&1
echo "[md $NAME] $(date) 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_lora_$NAME/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3))")"
GPUS=$G1 eval/run_dialect_eval.sh $RUN/merged $NAME > logs/mdeval_$NAME.log 2>&1
for D in cadquery openscad glsl blender; do echo "[md $NAME] $D: $(cat eval/out/md_${NAME}_$D/summary.json 2>/dev/null | tr -d '\n ' | cut -c1-200)"; done
rm -rf $RUN/merged; echo "[md $NAME] DONE"
