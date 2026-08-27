#!/bin/bash
# 4-step smoke test of the 27B LoRA recipe on another node (the project dir is on shared wekafs, so
# scripts/env/models/data are all identical — only the GPUs differ).
cd /wekafs/ict/hx_624/llm-ft || exit 1
GPUS=${GPUS:-0,1,2,3} scripts/lf_train.sh configs/lf/lora_27b_v2.yaml max_steps=4 save_steps=100000 eval_steps=100000 \
  output_dir=/wekafs/ict/hx_624/llm-ft/runs/_smoke_$(hostname) > logs/smoke_$(hostname).log 2>&1
grep -oE "'train_runtime': [0-9.]+|out of memory|[0-9]/4 \[[0-9:]+<[0-9:]+, *[0-9.]+s/it" logs/smoke_$(hostname).log | tail -2
rm -rf runs/_smoke_$(hostname)
