#!/bin/bash
# Full-parameter SFT after LoRA queues + ckpt sweeps; uses GPUs 0,1,2 (ZeRO-3) if GPU 2 is free, else GPUs 0,1 with ZeRO-3 + CPU offload. Then eval, then the postponed v2b LoRA on GPU 0.
cd /wekafs/ict/hx_624/llm-ft
until grep -q "QUEUE_DONE gpu=0" logs/queue_gpu0.log 2>/dev/null && grep -q "QUEUE_DONE gpu=1" logs/queue_gpu1.log 2>/dev/null && grep -q "CKPT_SWEEP_DONE v7_ep4" logs/ckpt_sweep_main.log 2>/dev/null; do sleep 60; done
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1 | sort -n | tail -1)" -lt 10000 ]; do sleep 30; done
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_full_v1
if [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 2)" -lt 10000 ]; then
  echo "[full] $(date) GPU 2 free -> 3 GPUs, ZeRO-3 (no offload), grad_accum=3"
  GPUS=0,1,2 scripts/lf_train.sh configs/lf/qwen35_9b_full_sft.yaml gradient_accumulation_steps=3 > logs/train_full_v1.log 2>&1
else
  echo "[full] $(date) GPU 2 busy (other user) -> 2 GPUs, ZeRO-3 + CPU optimizer offload, grad_accum=4"
  GPUS=0,1 scripts/lf_train.sh configs/lf/qwen35_9b_full_sft.yaml deepspeed=/wekafs/ict/hx_624/llm-ft/configs/lf/ds_z3_offload.json gradient_accumulation_steps=4 > logs/train_full_v1.log 2>&1
fi
[ -f $RUN/train_results.json ] || { echo "[full] TRAIN FAILED"; tail -30 logs/train_full_v1.log | cut -c1-200; exit 1; }
echo "[full] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
GPUS=0 eval/run_eval_vllm.sh $RUN eval/out/qwen35_9b_full_v1 --no_think --max_new_tokens 6144 > logs/eval_full_v1.log 2>&1
echo "[full] $(date) eval done"; cat eval/out/qwen35_9b_full_v1/summary.json; echo FULL_FT_DONE
# postponed ablation
scripts/run_exp.sh 0 v2b_indist16k 2>&1 | tee -a logs/exp_v2b_indist16k.log
