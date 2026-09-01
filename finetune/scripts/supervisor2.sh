#!/bin/bash
# Keeps GPUs 0-3 busy: runs the pending jobs in scripts/queue.txt one at a time, restarts nothing that succeeded,
# and reports every state change to logs/supervisor.log. One line per job: "<label>|<command>".
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
Q=scripts/queue.txt; STATE=logs/.supervisor_done
touch $STATE
while true; do
  PENDING=""
  while IFS='|' read -r label cmd; do
    [ -z "${label:-}" ] && continue
    case "$label" in \#*) continue;; esac
    grep -qx "$label" $STATE || { PENDING="$label|$cmd"; break; }
  done < $Q
  if [ -z "$PENDING" ]; then
    echo "[sup] $(date) queue empty — idling (add a line to $Q to continue)"
    IDLE_MIN=0
    while [ -z "$PENDING" ]; do
      sleep 300; IDLE_MIN=$((IDLE_MIN+5))
      [ $((IDLE_MIN % 30)) -eq 0 ] && echo "[sup] WARNING $(date) idle ${IDLE_MIN}m with GPUs [$(scripts/free_gpus.sh)] free"
      while IFS='|' read -r label cmd; do
        [ -z "${label:-}" ] && continue; case "$label" in \#*) continue;; esac
        grep -qx "$label" $STATE || { PENDING="$label|$cmd"; break; }
      done < $Q
    done
  fi
  label="${PENDING%%|*}"; cmd="${PENDING#*|}"
  # wait until no training/inference of mine is running and the GPUs are idle (3 confirmations)
  STABLE=0
  while [ $STABLE -lt 3 ]; do
    # count only REAL trainer/inference processes (python/torchrun): a plain `pgrep -f` also matches
    # monitoring shells whose own command line contains these strings, which deadlocks the queue
    MINE=0
    for pid in $(pgrep -u $USER -f "llamafactory-cli train|generate_multi.py|generate_vllm.py" 2>/dev/null); do
      case "$(ps -o comm= -p $pid 2>/dev/null)" in python*|torchrun*|pt_main*) MINE=$((MINE+1));; esac
    done
    F=$(scripts/free_gpus.sh); N=$(echo "$F" | awk -F, 'NF&&$1!=""{print NF}')
    if [ "$MINE" -eq 0 ] && [ "${N:-0}" -ge 2 ]; then STABLE=$((STABLE+1)); else STABLE=0; fi
    sleep 30
  done
  # the wait above can last hours; another process may have finished this job meanwhile, so re-confirm the
  # decision instead of acting on one made before the wait (this is how mm_multisuite ran a second time)
  if grep -qx "$label" $STATE; then echo "[sup] $(date) skipping $label — completed while waiting"; continue; fi
  echo "[sup] $(date) starting: $label"
  bash -c "$cmd" >> logs/sup_$label.log 2>&1
  RC=$?
  if [ $RC -eq 0 ]; then echo "[sup] $(date) OK: $label"; else echo "[sup] $(date) FAILED (rc=$RC): $label — see logs/sup_$label.log"; fi
  echo "$label" >> $STATE
done
