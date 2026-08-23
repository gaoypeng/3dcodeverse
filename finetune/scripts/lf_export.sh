#!/bin/bash
# usage: ./lf_export.sh <export_yaml>
set -euo pipefail
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate lf
export HF_HOME=/wekafs/ict/hx_624/cache/huggingface DISABLE_VERSION_CHECK=1 CUDA_VISIBLE_DEVICES=""
CFG=$(readlink -f "$1"); cd /wekafs/ict/hx_624/tools/LLaMA-Factory && llamafactory-cli export "$CFG"
