#!/bin/bash
# mmvis unfreezes the vision tower; it trained but was never scored. Both decodings, because section 13.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_mmvis
[ -f $RUN/train_results.json ] || { echo "[mmviseval] training never finished"; exit 0; }
if [ ! -d $RUN/merged ]; then
  sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_mmvis.yaml
  scripts/lf_export.sh configs/lf/export_mmvis.yaml > logs/export_mmvis.log 2>&1 || { echo "[mmviseval] EXPORT FAILED: $(grep -iE 'error' logs/export_mmvis.log | tail -1 | cut -c1-150)"; exit 0; }
fi
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[mmviseval] no GPU with 40 GB free"; exit 0; }
echo "[mmviseval] $(date) on GPU $G"
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_mmvis_greedy 2>&1 | tail -9
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_mmvis_t07 --temp 0.7 --seed 1 2>&1 | tail -9
# the image path is the point of unfreezing the vision tower, so score it too
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
OUT=eval/out/q9b_mmvis_img; mkdir -p $OUT
python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4text_bench_prompts.jsonl --out $OUT \
  --max_new_tokens 32768 --max_model_len 40960 > logs/gen_mmvis_img.log 2>&1; tail -2 logs/gen_mmvis_img.log
N=$(ls $OUT | grep -vc extract_stats.json || true)
[ "${N:-0}" -lt 5 ] && { echo "[mmviseval] image path PRODUCED NOTHING — not scoring"; exit 0; }
conda activate llmft
python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
echo "[mmviseval] DONE"
