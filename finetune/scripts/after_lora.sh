#!/bin/bash
# wait for LoRA run to finish -> merge adapter -> eval merged model on 3DCodeBench
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_v1
cd /wekafs/ict/hx_624/llm-ft
until [ -f $RUN/train_results.json ] || ! pgrep -f "configs/lf/qwen35_9b_lora_sft.yaml" >/dev/null; do sleep 20; done
[ -f $RUN/train_results.json ] || { echo "LORA_TRAIN_DID_NOT_FINISH"; exit 1; }
echo "[after_lora] train finished: $(cat $RUN/train_results.json | tr -d '\n')"
scripts/lf_export.sh configs/lf/export_lora_template.yaml > logs/export_lora_v1.log 2>&1 && echo "[after_lora] export done" || { echo "EXPORT_FAILED"; exit 1; }
ls $RUN/merged | head
GPUS=${EVAL_GPUS:-1} eval/run_eval.sh $RUN/merged eval/out/qwen35_9b_lora_v1 --no_think --batch_size 48 --max_new_tokens 6144 > logs/eval_qwen35_9b_lora_v1.log 2>&1
echo "[after_lora] eval done"; cat eval/out/qwen35_9b_lora_v1/summary.json; echo AFTER_LORA_DONE
