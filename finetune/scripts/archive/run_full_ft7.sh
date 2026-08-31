#!/bin/bash
# Full-parameter SFT: start as soon as the GPU-0 queue is done; use whichever of GPUs 0,1,2 are free (3 -> ZeRO-3, 2 -> ZeRO-3 + CPU offload). Then eval.
cd /wekafs/ict/hx_624/llm-ft
until grep -q "\[exp v2b_indist16k\] .*gen done" logs/exp_v2b_indist16k.log 2>/dev/null && grep -q "\[exp v8_r16\] .*gen done" logs/exp_v8_r16.log 2>/dev/null; do sleep 30; done; sleep 30
while true; do
  FREE=$(for g in 0 1 2; do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i $g)" -lt 10000 ] && echo -n "$g,"; done | sed 's/,$//')
  N=$(echo $FREE | tr ',' '\n' | grep -c .)
  [ "$N" -ge 2 ] && break; sleep 30
done
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_full_v1
if [ "$N" -ge 3 ]; then
  echo "[full] $(date) GPUs $FREE -> ZeRO-3 (no offload), grad_accum=3"
  GPUS=$FREE scripts/lf_train.sh configs/lf/qwen35_9b_full_sft.yaml gradient_accumulation_steps=3 > logs/train_full_v1.log 2>&1
else
  echo "[full] $(date) GPUs $FREE -> ZeRO-3 + CPU optimizer offload, grad_accum=4"
  GPUS=$FREE scripts/lf_train.sh configs/lf/qwen35_9b_full_sft.yaml deepspeed=/wekafs/ict/hx_624/llm-ft/configs/lf/ds_z3_offload.json gradient_accumulation_steps=4 > logs/train_full_v1.log 2>&1
fi
[ -f $RUN/train_results.json ] || { echo "[full] TRAIN FAILED"; grep -E "Error|error" logs/train_full_v1.log | tail -5 | cut -c1-200; exit 1; }
echo "[full] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
G1=$(echo $FREE | cut -d, -f1)
GPUS=$G1 eval/run_eval_vllm.sh $RUN eval/out/qwen35_9b_full_v1 --no_think --max_new_tokens 6144 > logs/eval_full_v1.log 2>&1
echo "[full] $(date) eval done"; cat eval/out/qwen35_9b_full_v1/summary.json; echo FULL_FT_DONE
