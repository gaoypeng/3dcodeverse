#!/bin/bash
# Does training on reference images change what the model can do in dialects that had NO image data?
# mm_img_text and mm_text were trained on identical samples and the identical split, differing only in whether a
# render was shown — so any gap on the five held-out dialect suites is attributable to the images alone.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
for V in img_text text; do
  RUN=runs/lf_qwen35_9b_mm_$V
  [ -d "$RUN/merged" ] || { echo "[multisuite-$V] no merged model — skipping"; continue; }
  G=""; for _ in $(seq 1 40); do G=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G" ] && break; sleep 30; done
  [ -z "$G" ] && { echo "[multisuite-$V] skipped: no GPU with 40 GB free after 20 min"; continue; }
  echo "[multisuite-$V] $(date) all six suites on GPU $G"
  GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_mm_${V}_all 2>&1 | tail -25
  echo "[multisuite-$V] DONE"
done
echo "[multisuite] ALL DONE"
