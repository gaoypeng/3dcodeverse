#!/bin/bash
# 27B v2 is the project's other headline model and its dialect numbers are greedy-only, so by section 13 they
# understate it the same way the 9B's did (base 27B scores 88% on OpenSCAD, the fine-tune reads 34%).
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen38_27b_lora_v2
if [ ! -d $RUN/merged ]; then
  [ -f configs/lf/export_27b_v2.yaml ] || { echo "[27bdual] no export config"; exit 0; }
  scripts/lf_export.sh configs/lf/export_27b_v2.yaml > logs/export_27b_v2_dual.log 2>&1 \
    || { echo "[27bdual] EXPORT FAILED: $(grep -iE 'error|OSError' logs/export_27b_v2_dual.log | tail -1 | cut -c1-160)"; exit 0; }
fi
# a 51 GB checkpoint does not fit one shared card; two-way tensor parallel with the Mamba-cache cap it needs
G=${GPUS:-$(scripts/free_gpus.sh 45000 | cut -d, -f1,2)}
TP=$(echo "$G" | awk -F, '{print NF}')
{ [ -z "$G" ] || [ "${TP:-0}" -lt 2 ]; } && { echo "[27bdual] skipped: need two cards with 45 GB free, got [$G]"; exit 0; }
echo "[27bdual] $(date) on GPUs $G (tp=$TP)"
GPUS=$G TP=$TP bash eval/eval_all_run2.sh $RUN/merged q27b_v2_t07 --temp 0.7 --seed 1 2>&1 | tail -10
echo "[27bdual] DONE"
