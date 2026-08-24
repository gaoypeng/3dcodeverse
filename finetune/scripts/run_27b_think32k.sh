#!/bin/bash
# Fair-budget rerun: 27B with thinking ON but a 32k output budget on 3DCodeBench (the 12k run truncated 204/212).
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
until grep -qE "\[9bmax\] (ALL DONE|TRAIN FAILED|EXPORT FAILED)" logs/run_md_max_9b.log 2>/dev/null; do sleep 120; done
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1 | sort -n | tail -1)" -lt 8000 ]; do sleep 60; done
echo "[27b32k] $(date) bench with thinking, 32k budget"
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=0,1
python eval/generate_multi.py --model /wekafs/ict/hx_624/models/Qwen3.8-27B --tp 2 --max_new_tokens 30000 --max_model_len 34816 \
  --spec '[{"name":"bench","prompts":"data/sft_v1/bench_prompts.jsonl","out":"eval/out/q27b_think32k_bench","dialect":"blender"}]' 2>&1 | grep -E "gen-multi|extract\]" | tail -3
conda activate llmft; eval/exec_suites.sh q27b_think32k bench
echo "[27b32k] DONE"
