#!/usr/bin/env bash
# One full stop-DPO round: sample the model, pair its runaway greedy output against its own terminating
# sampled output, train, merge. Termination pairs transfer across models, so a round is worth running on any
# new SFT model. Replaces run_stop_dpo{,_r2,_train}.sh and run_stopdpo_{r2_plus,transfer}.sh, which differed
# only in which model and tag they hardcoded.
#   scripts/stop_dpo_round.sh <base_model_dir> <tag>
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
BASE=$(readlink -f "$1"); TAG=$2; RUN=runs/lf_${TAG}
[ -d "$BASE" ] || { echo "[stopdpo] base model $BASE missing"; exit 1; }
export TAG BASE_MODEL="$BASE"
source /wekafs/ict/hx_624/.secrets/hf.env 2>/dev/null || true
bash scripts/stop_dpo_pairs.sh
PAIRS=data/lf/stop_dpo_pairs_${TAG}.json
N=$(python3 -c "import json;print(len(json.load(open('$PAIRS'))))" 2>/dev/null || echo 0)
echo "[stopdpo] $N pairs"
# Round 1 removes most runaways; too few left means the behaviour is gone, and that is the result.
[ "${N:-0}" -lt 100 ] && { echo "[stopdpo] only $N pairs left — stopping here"; exit 0; }
CFG=configs/lf/_gen_dpo_${TAG}.yaml
sed -e "s|^model_name_or_path:.*|model_name_or_path: $BASE|" \
    -e "s|^dataset:.*|dataset: stop_dpo_pairs_${TAG}|" \
    -e "s|^output_dir:.*|output_dir: $(pwd)/$RUN|" configs/lf/dpo_9b_lora16_lr5e-6_len2k_stop-r1.yaml > "$CFG"
scripts/train_when_free.sh "$CFG" 60000 "logs/train_${TAG}.log" || exit 1
scripts/export_lora.sh "$RUN" "$BASE" > "logs/export_${TAG}.log" 2>&1
echo "[stopdpo] DONE -> $RUN/merged"
