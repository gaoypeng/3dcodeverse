#!/bin/bash
# Every dialect number in the report was measured greedy, and section 13 showed that understates the long-output
# dialects badly (9B OpenSCAD 6% vs 44%, 27B 34% vs 80%). Re-score the models whose merged weights still exist,
# one after another in a single session so the numbers are comparable to each other (13.7).
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
for SPEC in "runs/lf_qwen35_9b_lora_md_xl:q9b_md_xl_t07" "runs/lf_qwen35_9b_imgmix:q9b_imgmix_t07" "runs/lf_qwen35_9b_lora_v1:q9b_v1_t07"; do
  M="${SPEC%%:*}"; NAME="${SPEC##*:}"
  [ -d "$M/merged" ] || { echo "[rescore] $M has no merged weights — skipping (the DPO scripts delete them)"; continue; }
  G=""; for _ in $(seq 1 90); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 60; done
  [ -z "$G" ] && { echo "[rescore] no GPU with 40 GB free"; exit 0; }
  echo "[rescore] $(date) $NAME on GPU $G"
  GPUS=$G TP=1 bash eval/eval_all_run2.sh $M/merged $NAME --temp 0.7 --seed 1 2>&1 | tail -9
done
echo "[rescore] DONE"
