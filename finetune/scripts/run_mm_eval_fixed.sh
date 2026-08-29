#!/bin/bash
# Re-evaluate the three mm_ablation variants under one honest setting.
# The first pass was invalid twice over: a 6144-token output cap turned long-but-correct programs into syntax
# errors, and the pre-eval wait demanded a trainer's 60 GB free, so on this shared box it either skipped or
# launched vLLM into memory another user already held. Both are fixed here; results from this script supersede
# whatever logs/sup_mm_ablation.log reported.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
for V in img_text img text; do
  RUN=runs/lf_qwen35_9b_mm_$V
  [ -d "$RUN/merged" ] || { echo "[mmeval-$V] no merged model at $RUN/merged — skipping"; continue; }
  # an eval needs room for a 9B plus its KV cache, not a trainer's working set
  G1=""; for _ in $(seq 1 40); do G1=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G1" ] && break; sleep 30; done
  [ -z "$G1" ] && { echo "[mmeval-$V] skipped: no GPU with 40 GB free after 20 min — $(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader -i 0,1,2,3 | tr '\n' ' ')"; continue; }
  OUT=eval/out/q9b_mm_${V}_bench_fix; mkdir -p $OUT
  echo "[mmeval-$V] $(date) generating on GPU $G1 -> $OUT"
  conda activate vllm
  export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G1
  case $V in
    text)     python eval/generate_vllm.py --model $RUN/merged --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp 1 --no_think --dialect blender --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -3;;
    img)      python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4_bench_prompts.jsonl     --out $OUT --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -3;;
    img_text) python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4text_bench_prompts.jsonl --out $OUT --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -3;;
  esac
  N=$(ls $OUT 2>/dev/null | grep -vc extract_stats.json || true)
  # zero generations means the engine never came up; say so instead of scoring an empty directory as 0%
  [ "${N:-0}" -lt 5 ] && { echo "[mmeval-$V] GENERATION PRODUCED NOTHING ($N dirs) — not scoring"; continue; }
  conda activate llmft
  python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
  python eval/metrics.py --gen_dir $OUT 2>&1 | tail -1
  echo "[mmeval-$V] truncated=$(python -c "import json;print(json.load(open('$OUT/extract_stats.json')).get('truncated'))" 2>/dev/null)"
  echo "[mmeval-$V] DONE"
done
echo "[mmeval] ALL DONE"
