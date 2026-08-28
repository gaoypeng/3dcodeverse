#!/bin/bash
# Image-conditioned pilot: render -> code. The qwen3_5 template already carries the qwen3_vl multimodal plugin,
# so this is the first run in the project that feeds the model what a caption cannot express — the target's shape.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
G=$(scripts/free_gpus.sh); N=$(echo "$G" | awk -F, '{print NF}')
echo "[9bimgmix] $(date) smoke on GPUs $G"
GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_imgmix.yaml max_steps=6 save_steps=100000 eval_steps=100000 \
  output_dir=/wekafs/ict/hx_624/llm-ft/runs/_smoke_imgmix4 > logs/smoke_imgmix.log 2>&1
if ! grep -q train_runtime logs/smoke_imgmix.log; then
  echo "[9bimgmix] SMOKE FAILED: $(grep -iE 'error|Traceback' logs/smoke_imgmix.log | grep -v errors | tail -2 | head -1 | cut -c1-200)"; exit 1
fi
rm -rf runs/_smoke_imgmix4; echo "[9bimgmix] smoke ok: $(grep -oE "'train_runtime': [0-9.]+" logs/smoke_imgmix.log | tail -1)"
echo "[9bimgmix] $(date) train (7,997 4-view render->code samples, 2 epochs)"
GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_imgmix.yaml > logs/train_9b_imgmix.log 2>&1
RUN=runs/lf_qwen35_9b_imgmix
[ -f $RUN/train_results.json ] || { echo "[9bimgmix] TRAIN FAILED: $(grep -iE 'out of memory|error' logs/train_9b_imgmix.log | grep -v errors | tail -1 | cut -c1-180)"; exit 1; }
echo "[9bimgmix] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_imgmix.yaml
scripts/lf_export.sh configs/lf/export_9b_imgmix.yaml > logs/export_9b_imgmix.log 2>&1 || { echo "[9bimgmix] EXPORT FAILED"; exit 1; }
echo "[9bimgmix] ALL DONE"
