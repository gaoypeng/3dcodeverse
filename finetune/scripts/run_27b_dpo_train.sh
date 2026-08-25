#!/bin/bash
# Resume the 27B execution-feedback DPO round from the already-built pairs (756 pairs, data/lf/dpo_27b_pairs.json).
# DPO forwards chosen+rejected, so a plain DDP LoRA OOMs at 27B; the token count here is tiny, so ZeRO-3 is the right trade.
set -uo pipefail
cd /wekafs/ict/hx_624/llm-ft
BASE=runs/lf_qwen38_27b_lora_v2/merged
RUN=runs/lf_qwen38_27b_dpo

STABLE=0; GPUS_USE=""
while [ $STABLE -lt 2 ]; do
  F=$(scripts/free_gpus.sh); N=$(echo "$F" | awk -F, 'NF&&$1!=""{print NF}')
  if [ -n "$F" ] && [ "${N:-0}" -ge 2 ]; then
    if [ "$F" = "$GPUS_USE" ]; then STABLE=$((STABLE+1)); else GPUS_USE="$F"; STABLE=1; fi
  else STABLE=0; GPUS_USE=""; fi
  sleep 30
done
NGPU=$(echo "$GPUS_USE" | awk -F, '{print NF}')
echo "[27bdpo] $(date) DPO train on GPUs $GPUS_USE (ZeRO-3, cutoff 2048)"
GPUS=$GPUS_USE scripts/lf_train.sh configs/lf/dpo_27b.yaml cutoff_len=2048 gradient_accumulation_steps=4 \
  deepspeed=/wekafs/ict/hx_624/tools/LLaMA-Factory/examples/deepspeed/ds_z3_config.json > logs/train_27b_dpo.log 2>&1
if [ ! -f $RUN/train_results.json ]; then
  echo "[27bdpo] TRAIN FAILED: $(grep -E 'OutOfMemory|out of memory|Error' logs/train_27b_dpo.log | grep -v errors | tail -1 | cut -c1-160)"
  exit 1
fi
echo "[27bdpo] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" \
    -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" \
    -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_dpo.yaml
scripts/lf_export.sh configs/lf/export_27b_dpo.yaml > logs/export_27b_dpo.log 2>&1 || { echo "[27bdpo] EXPORT FAILED"; exit 1; }
echo "[27bdpo] $(date) eval"
GPUS=$GPUS_USE TP=$NGPU eval/eval_all_run2.sh $RUN/merged q27b_dpo --max_new 8192 2>&1 | grep -E "gen-multi|status counts|dialect|DONE" | tail -20
GPUS=$GPUS_USE TP=$NGPU eval/eval_all_run2.sh $RUN/merged q27b_dpo_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "dialect|DONE" | tail -4
echo "[27bdpo] ALL DONE"
