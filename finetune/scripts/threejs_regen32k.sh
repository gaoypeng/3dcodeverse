#!/bin/bash
# re-merge the three.js LoRA (merged dir was cleaned up), re-generate with a 32k budget on GPU 2, evaluate
cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_lora_md_threejs
[ -f $RUN/merged/config.json ] || scripts/lf_export.sh configs/lf/export_md_threejs.yaml > logs/export_md_threejs_2.log 2>&1 || { echo "[3js32k] EXPORT FAILED"; exit 1; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=2 PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright
echo "[3js32k] $(date) gen LoRA 32k"
mkdir -p eval/out/md_md_threejs_threejs_32k
python eval/generate_vllm.py --model $RUN/merged --prompts eval/out/_3js_prompts.jsonl --out eval/out/md_md_threejs_threejs_32k --tp 1 --no_think --max_new_tokens 30000 --max_model_len 32768 2>&1 | grep -E "gen-vllm|Error" | tail -1
conda activate llmft
python eval/threejs_eval.py --gen_dir eval/out/md_md_threejs_threejs_32k 2>&1 | tail -2
echo "[3js32k] done2"
