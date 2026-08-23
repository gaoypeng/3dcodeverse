#!/bin/bash
# GPU 0 while DPO (GPUs 2,3) and sweep (GPU 1) run: three.js eval (greedy + T=0.7) and 3DCodeBench best-of-4 for md_xl (uses the merged model re-exported by the DPO round)
cd /wekafs/ict/hx_624/llm-ft; M=runs/lf_qwen35_9b_lora_md_xl/merged
until [ -f data/md_xl_dpo_round/gen_s1_g0.log ] && [ -f $M/config.json ]; do sleep 30; done; sleep 60
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=0 PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright
echo "[xlextra] $(date) three.js gens"
mkdir -p eval/out/md_md_xl_threejs eval/out/md_md_xl_T07_threejs
python eval/generate_vllm.py --model $M --prompts eval/out/_3js_prompts.jsonl --out eval/out/md_md_xl_threejs --tp 1 --no_think --max_new_tokens 12288 2>&1 | grep -E "gen-vllm|Error" | tail -1
python eval/generate_vllm.py --model $M --prompts eval/out/_3js_prompts.jsonl --out eval/out/md_md_xl_T07_threejs --tp 1 --no_think --max_new_tokens 12288 --temperature 0.7 --seed 1 2>&1 | grep -E "gen-vllm|Error" | tail -1
echo "[xlextra] $(date) best-of-4"
GPUS=0 eval/best_of_n.sh $M md_xl_T07 4 0.7 2>&1 | grep -E "pass@|OK rate|BEST" 
conda activate llmft
for N in md_md_xl_threejs md_md_xl_T07_threejs; do echo "== $N"; python eval/threejs_eval.py --gen_dir eval/out/$N 2>&1 | tail -2; done
echo "[xlextra] DONE"
