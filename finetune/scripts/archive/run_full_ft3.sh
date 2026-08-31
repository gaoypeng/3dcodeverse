#!/bin/bash
# Full-parameter SFT (ZeRO-3) on GPUs 0,1,2 after all LoRA queues finish, then eval.
cd /wekafs/ict/hx_624/llm-ft
until grep -q "QUEUE_DONE gpu=0" logs/queue_gpu0.log 2>/dev/null && grep -q "QUEUE_DONE gpu=1" logs/queue_gpu1.log 2>/dev/null && grep -q "QUEUE_DONE gpu=2" logs/exp_v3_bio_retry.log 2>/dev/null && grep -q "CKPT_SWEEP_DONE v7_ep4" logs/ckpt_sweep_main.log 2>/dev/null; do sleep 60; done
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1,2 | sort -n | tail -1)" -lt 10000 ]; do sleep 30; done
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_full_v1
echo "[full] $(date) start full FT on GPUs 0,1,2"
GPUS=0,1,2 scripts/lf_train.sh configs/lf/qwen35_9b_full_sft.yaml > logs/train_full_v1.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[full] TRAIN FAILED"; exit 1; }
echo "[full] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
if [ -f eval/.vllm_ok ]; then GPUS=0 eval/run_eval_vllm.sh $RUN eval/out/qwen35_9b_full_v1 --no_think --max_new_tokens 6144 > logs/eval_full_v1.log 2>&1
else GPUS=0,1,2 eval/run_eval.sh $RUN eval/out/qwen35_9b_full_v1 --no_think --batch_size 48 --max_new_tokens 6144 > logs/eval_full_v1.log 2>&1; fi
echo "[full] $(date) eval done"; cat eval/out/qwen35_9b_full_v1/summary.json; echo FULL_FT_DONE
