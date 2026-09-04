#!/bin/bash
# Print the GPUs with at least <need_free_mib> free — other users share this box, so "completely empty" is the
# wrong test; what matters is whether OUR job fits.
#   scripts/free_gpus.sh [need_free_mib] [gpu_list]    default 60000 (a 9B LoRA at cutoff 8192), all GPUs
# The list used to be hardcoded to 0-3 from when this box exposed four cards, which silently hid 4-7 from every
# script that auto-selects a GPU.
NEED=${1:-60000}
LIST=${2:-$(nvidia-smi --query-gpu=index --format=csv,noheader | tr '\n' ' ')}
OUT=""
for i in $LIST; do
  read -r TOT USED <<< "$(nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits -i $i | tr ',' ' ')"
  [ -z "${TOT:-}" ] && continue
  FREE=$((TOT - USED))
  [ "$FREE" -ge "$NEED" ] && OUT="$OUT,$i"
done
echo "${OUT#,}"
