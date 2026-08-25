#!/bin/bash
# print the GPU ids (of 0-3) that are essentially empty; usage: FREE=$(scripts/free_gpus.sh [min_free_mib])
THRESH=${1:-4000}
OUT=""
for i in 0 1 2 3; do
  M=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i $i)
  [ "$M" -lt "$THRESH" ] && OUT="$OUT,$i"
done
echo "${OUT#,}"
