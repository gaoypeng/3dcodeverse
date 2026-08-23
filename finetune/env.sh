# source this before running anything in the project
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
conda activate llmft
source /wekafs/ict/hx_624/.secrets/hf.env
export WORKSPACE=/wekafs/ict/hx_624
export HF_HOME=$WORKSPACE/cache/huggingface HF_XET_HIGH_PERFORMANCE=1
export TRITON_CACHE_DIR=$WORKSPACE/cache/triton TORCH_EXTENSIONS_DIR=$WORKSPACE/cache/torch_extensions
export PIP_CACHE_DIR=$WORKSPACE/cache/pip CUDA_HOME=/usr/local/cuda-12.8
export TOKENIZERS_PARALLELISM=false
mkdir -p $TRITON_CACHE_DIR $TORCH_EXTENSIONS_DIR
export VLLM_CACHE_ROOT=$WORKSPACE/cache/vllm
