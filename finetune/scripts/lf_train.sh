#!/bin/bash
# usage: GPUS=0,1,2 ./lf_train.sh configs/lf/<cfg>.yaml [extra llamafactory-cli overrides: key=value ...]
set -euo pipefail
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate lf
source /wekafs/ict/hx_624/.secrets/hf.env
export WORKSPACE=/wekafs/ict/hx_624 HF_HOME=/wekafs/ict/hx_624/cache/huggingface HF_XET_HIGH_PERFORMANCE=1
export TORCH_EXTENSIONS_DIR=$WORKSPACE/cache/torch_extensions CUDA_HOME=/usr/local/cuda-12.8
# per-run Triton cache on LOCAL disk (shared cache on wekafs races between concurrent runs); seed it from the shared cache to avoid recompiling
TC=/tmp/hx_624_triton/$(basename "$(python -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['output_dir'])" "$(readlink -f "$1")")")
[ -d "$TC" ] || { mkdir -p /tmp/hx_624_triton && cp -r $WORKSPACE/cache/triton "$TC" 2>/dev/null || mkdir -p "$TC"; }
export TRITON_CACHE_DIR=$TC
export TOKENIZERS_PARALLELISM=false DISABLE_VERSION_CHECK=1
export CUDA_VISIBLE_DEVICES=${GPUS:-0,1}
CFG=$(readlink -f "$1"); shift
RUN=$(python -c "import yaml,sys; print(yaml.safe_load(open('$CFG'))['output_dir'])")
mkdir -p $RUN
echo "[lf_train] cfg=$CFG gpus=$CUDA_VISIBLE_DEVICES out=$RUN"
cd /wekafs/ict/hx_624/tools/LLaMA-Factory
FORCE_TORCHRUN=1 MASTER_PORT=$((29600 + RANDOM % 300)) llamafactory-cli train $CFG "$@" 2>&1 | tee -a $RUN/train.log
