#!/bin/bash
cd /wekafs/ict/hx_624/llm-ft; RUN=runs/lf_qwen35_9b_lora_md_threejs
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=2 PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright
for M in "$RUN/merged md_md_threejs_T07_threejs" "/wekafs/ict/hx_624/models/Qwen3.5-9B md_base_T07_threejs"; do set -- $M
  mkdir -p eval/out/$2; python eval/generate_vllm.py --model $1 --prompts eval/out/_3js_prompts.jsonl --out eval/out/$2 --tp 1 --no_think --max_new_tokens 12288 --temperature 0.7 --seed 1 2>&1 | grep -E "gen-vllm|Error" | tail -1
done
conda activate llmft
for N in md_md_threejs_T07_threejs md_base_T07_threejs; do echo "== $N"; python eval/threejs_eval.py --gen_dir eval/out/$N 2>&1 | tail -2; done
echo "[3jsT07] done"
