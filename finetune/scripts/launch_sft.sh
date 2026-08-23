#!/bin/bash
# usage: GPUS=0,1 ./launch_sft.sh <run_name> [extra train_sft.py args...]
# Runs torchrun on the given GPUs (must be a subset of 0-3 per the user's rule).
set -euo pipefail
source /wekafs/ict/hx_624/llm-ft/env.sh
export CUDA_VISIBLE_DEVICES=${GPUS:-0,1}
RUN=$1; shift
NGPU=$(echo $CUDA_VISIBLE_DEVICES | tr ',' '\n' | wc -l)
OUT=/wekafs/ict/hx_624/llm-ft/runs/$RUN
mkdir -p $OUT
PORT=$((29500 + RANDOM % 500))
echo "[launch] run=$RUN gpus=$CUDA_VISIBLE_DEVICES ngpu=$NGPU port=$PORT out=$OUT"
cd /wekafs/ict/hx_624/llm-ft
torchrun --nproc_per_node=$NGPU --master_port=$PORT scripts/train_sft.py --output_dir $OUT "$@" 2>&1 | tee -a $OUT/train.log
