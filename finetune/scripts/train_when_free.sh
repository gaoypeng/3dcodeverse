#!/usr/bin/env bash
# Launch a LLaMA-Factory config as soon as a card is genuinely free, and survive losing the race.
# free_gpus.sh can name a card that another user fills during the ~2 min of model loading; that
# surfaces as a CUDA OOM minutes later, which is how the round-2 stop-DPO run died.
#   scripts/train_when_free.sh <config.yaml> [need_free_mib] [logfile]
set -u
CFG=$1; NEED=${2:-60000}; LOG=${3:-logs/$(basename "${CFG%.yaml}").log}
for attempt in $(seq 1 200); do
  G=$(scripts/free_gpus.sh "$NEED" | cut -d, -f1)
  if [ -n "$G" ]; then
    sleep 45                                                   # let any competing job finish allocating
    STILL=$(scripts/free_gpus.sh "$NEED" "$G" | cut -d, -f1)    # ...and confirm the card is still free
    if [ -n "$STILL" ]; then
      echo "[twf] attempt $attempt: GPU $G <- $(basename "$CFG")"
      GPUS=$G scripts/lf_train.sh "$CFG" > "$LOG" 2>&1 && { echo "[twf] DONE"; exit 0; }
      if grep -qi "OutOfMemory" "$LOG"; then
        echo "[twf] lost GPU $G to another user; will retry"; cp "$LOG" "$LOG.oom.$attempt"; sleep 300; continue
      fi
      echo "[twf] FAILED (not OOM) — see $LOG"; exit 1
    fi
  fi
  sleep 120
done
