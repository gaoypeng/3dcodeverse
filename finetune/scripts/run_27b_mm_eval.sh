#!/bin/bash
# Score the 27B multimodal LoRA once its 4h16m single-GPU run finishes. It needs a card with room for a 27B
# (~55 GB of weights), which on this box means a nearly empty one.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen38_27b_mm_img_text
[ -f $RUN/train_results.json ] || { echo "[27bmmeval] training has not finished — nothing to score"; exit 0; }
if [ ! -d $RUN/merged ]; then
  sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_mm.yaml
  scripts/lf_export.sh configs/lf/export_27b_mm.yaml > logs/export_27b_mm.log 2>&1 || { echo "[27bmmeval] EXPORT FAILED"; exit 0; }
fi
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 70000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 60; done
[ -z "$G" ] && { echo "[27bmmeval] skipped: a 27B needs ~70 GB free and no card had it"; exit 0; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
OUT=eval/out/q27b_mm_img_text_bench; mkdir -p $OUT
python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4text_bench_prompts.jsonl --out $OUT \
  --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -1
N=$(ls $OUT | grep -vc extract_stats.json || true)
[ "${N:-0}" -lt 5 ] && { echo "[27bmmeval] GENERATION PRODUCED NOTHING — not scoring"; exit 0; }
conda activate llmft
python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
echo "[27bmmeval] DONE"
