#!/bin/bash
# wait LoRA-v1 -> merge -> test vLLM on GPU 0 -> eval merged model with vLLM (GPU 0); fallback: HF sharded eval on GPUs 0,2
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_v1; cd /wekafs/ict/hx_624/llm-ft
until [ -f $RUN/train_results.json ] || ! pgrep -f "configs/lf/qwen35_9b_lora_sft.yaml" >/dev/null; do sleep 20; done
[ -f $RUN/train_results.json ] || { echo "LORA_TRAIN_DID_NOT_FINISH"; exit 1; }
echo "[after_lora] train finished: $(tr -d '\n' < $RUN/train_results.json)"
scripts/lf_export.sh configs/lf/export_lora_template.yaml > logs/export_lora_v1.log 2>&1 && echo "[after_lora] export done" || { echo "EXPORT_FAILED"; exit 1; }
echo "[after_lora] vllm smoke test on GPU 0"
if GPUS=0 eval/run_eval_vllm.sh /wekafs/ict/hx_624/models/Qwen3.5-9B eval/out/_vllm_smoke --no_think --limit 8 --max_new_tokens 1024 > logs/vllm_smoke.log 2>&1; then
  echo "[after_lora] VLLM_OK -> eval merged model with vLLM on GPU 0"
  GPUS=0 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_lora_v1 --no_think --max_new_tokens 6144 > logs/eval_qwen35_9b_lora_v1.log 2>&1
else
  echo "[after_lora] VLLM_FAILED -> HF eval on GPUs 0,2"; tail -20 logs/vllm_smoke.log
  GPUS=0,2 eval/run_eval.sh $RUN/merged eval/out/qwen35_9b_lora_v1 --no_think --batch_size 48 --max_new_tokens 6144 > logs/eval_qwen35_9b_lora_v1.log 2>&1
fi
echo "[after_lora] eval done"; cat eval/out/qwen35_9b_lora_v1/summary.json; echo AFTER_LORA_DONE
