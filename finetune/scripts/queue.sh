#!/bin/bash
# queue.sh <GPU> <NAME1> [NAME2 ...]: run experiments sequentially on one GPU (waits until no training of ours is using that GPU)
GPU=$1; shift
cd /wekafs/ict/hx_624/llm-ft
for NAME in "$@"; do
  # wait until every GPU in the list has < 10 GB used (previous job finished)
  until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i $GPU | sort -n | tail -1)" -lt 10000 ]; do sleep 30; done
  scripts/run_exp.sh $GPU $NAME 2>&1 | tee -a logs/exp_$NAME.log
done
echo "QUEUE_DONE gpu=$GPU"
