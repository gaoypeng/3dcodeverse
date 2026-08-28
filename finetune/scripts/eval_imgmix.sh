#!/bin/bash
# The image+text model must be checked on BOTH paths: does adding vision cost anything on plain text prompts,
# and does joint training beat the image-only model when given a render?
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_imgmix; G=$(scripts/free_gpus.sh)
[ -f $RUN/merged/config.json ] || { echo "[imgmix] merged model missing"; exit 1; }
echo "[imgmixeval] $(date) text path"
GPUS=$(echo $G|cut -d, -f1) TP=1 eval/eval_all_run2.sh $RUN/merged q9b_imgmix --max_new 8192 --suites bench blender cadquery 2>&1 | grep -E "status counts|dialect|DONE" | tail -6
echo "[imgmixeval] $(date) image path"
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$(echo $G|cut -d, -f1)
OUT=eval/out/q9b_imgmix_img_bench; mkdir -p $OUT
python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img_bench_prompts.jsonl --out $OUT --max_new_tokens 6144 2>&1 | tail -1
conda activate llmft
python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
python eval/metrics.py --gen_dir $OUT 2>&1 | tail -1
echo "[imgmixeval] ALL DONE"
