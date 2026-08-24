#!/bin/bash
# 8-step smoke test of a 27B LoRA config to pick DDP vs ZeRO-3 before committing hours
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft; CFG=$1; TAG=$2
GPUS=0,1,2,3 scripts/lf_train.sh $CFG max_steps=8 num_train_epochs=1.0 save_steps=100000 eval_steps=100000 \
  tokenized_path=/wekafs/ict/hx_624/llm-ft/data/lf/tokenized_md_27b_len8192_packed \
  output_dir=/wekafs/ict/hx_624/llm-ft/runs/_smoke_$TAG > logs/smoke_$TAG.log 2>&1
if grep -q "train_runtime" logs/smoke_$TAG.log; then
  echo "[smoke $TAG] OK $(grep -oE '[0-9]+/8 \[[0-9:]+<[0-9:]+, *[0-9.]+s/it' logs/smoke_$TAG.log | tail -1)"
  grep -oE "'train_runtime': [0-9.]+" logs/smoke_$TAG.log | tail -1
else
  echo "[smoke $TAG] FAILED: $(grep -E 'OutOfMemoryError|Error|RuntimeError' logs/smoke_$TAG.log | grep -v errors | tail -1 | cut -c1-160)"
fi
rm -rf runs/_smoke_$TAG
