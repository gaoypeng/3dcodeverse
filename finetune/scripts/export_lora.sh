#!/usr/bin/env bash
# Merge a LoRA run into <run_dir>/merged. Replaces the 17 per-run export_*.yaml files that used to differ
# only in three paths.
#   scripts/export_lora.sh <run_dir> [base_model]
set -euo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=$(readlink -f "$1"); BASE=${2:-/wekafs/ict/hx_624/models/Qwen3.5-9B}
CFG=$(mktemp /tmp/export_XXXX.yaml)
sed -e "s|^model_name_or_path:.*|model_name_or_path: $BASE|" \
    -e "s|^adapter_name_or_path:.*|adapter_name_or_path: $RUN|" \
    -e "s|^export_dir:.*|export_dir: $RUN/merged|" configs/lf/export_lora_template.yaml > "$CFG"
scripts/lf_export.sh "$CFG"; rm -f "$CFG"
echo "[export] $RUN/merged"
