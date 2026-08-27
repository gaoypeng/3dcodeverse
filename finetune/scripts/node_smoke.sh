#!/bin/bash
# smoke a config on whatever node/GPUs we are given: scripts/node_smoke.sh <config.yaml> <tag> [steps]
cd /wekafs/ict/hx_624/llm-ft || exit 1
CFG=$1; TAG=$2; N=${3:-4}
GPUS=${GPUS:-0,1,2,3} scripts/lf_train.sh $CFG max_steps=$N save_steps=100000 eval_steps=100000 \
  output_dir=/wekafs/ict/hx_624/llm-ft/runs/_smoke_$TAG > logs/smoke_$TAG.log 2>&1
grep -oE "'train_runtime': [0-9.]+|out of memory|[0-9]/$N \[[0-9:]+<[0-9:]+, *[0-9.]+s/it" logs/smoke_$TAG.log | tail -2
rm -rf runs/_smoke_$TAG
