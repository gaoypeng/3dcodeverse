#!/bin/bash
# Print the GPUs (of 0-3) with at least <need_free_mib> free — other users share this box, so "completely empty"
# is the wrong test; what matters is whether OUR job fits.
#   scripts/free_gpus.sh [need_free_mib]     default 60000 (a 9B LoRA at cutoff 8192)
NEED=${1:-60000}
OUT=""
for i in 0 1 2 3; do
  read -r TOT USED <<< "$(nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits -i $i | tr ',' ' ')"
  FREE=$((TOT - USED))
  [ "$FREE" -ge "$NEED" ] && OUT="$OUT,$i"
done
echo "${OUT#,}"
