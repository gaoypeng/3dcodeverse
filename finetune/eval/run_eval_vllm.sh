#!/bin/bash
# usage: GPUS=0 ./run_eval_vllm.sh <model_path> <out_dir> [extra generate_vllm.py args]  (TP = number of GPUs listed)
set -euo pipefail
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
source /wekafs/ict/hx_624/.secrets/hf.env; export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING TOKENIZERS_PARALLELISM=false
MODEL=$(readlink -f "$1"); OUT=$(readlink -m "$2"); shift 2
export CUDA_VISIBLE_DEVICES=${GPUS:-0}; TP=$(echo $CUDA_VISIBLE_DEVICES | tr ',' '\n' | wc -l)
PROMPTS=${PROMPTS:-/wekafs/ict/hx_624/llm-ft/data/sft_v1/bench_prompts.jsonl}
mkdir -p $OUT; echo "[eval-vllm] model=$MODEL out=$OUT gpus=$CUDA_VISIBLE_DEVICES tp=$TP"
python /wekafs/ict/hx_624/llm-ft/eval/generate_vllm.py --model $MODEL --prompts $PROMPTS --out $OUT --tp $TP "$@" 2>&1 | grep -vE "^\s*$" | tail -n 40
cp $OUT/gens_shard0.jsonl $OUT/gens.jsonl
conda activate llmft
python /wekafs/ict/hx_624/llm-ft/eval/run_bench.py --gen_dir $OUT --workers ${WORKERS:-32} --timeout ${TIMEOUT:-300} 2>&1 | tee $OUT/exec.log
python /wekafs/ict/hx_624/llm-ft/eval/metrics.py --gen_dir $OUT 2>&1 | tee $OUT/metrics.log
