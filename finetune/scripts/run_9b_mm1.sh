#!/bin/bash
# 12.8 found one reference view beats four at INFERENCE (64.6% vs 47.6%) on weights trained with four views.
# Does it also hold when the model is TRAINED on one view? Same samples, same split, same everything but the
# number of renders per sample -- so the comparison against mm_img_text (48.6%) is clean.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
V=mm1_img_text; RUN=runs/lf_qwen35_9b_$V
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh)}; [ -n "$G" ] && break; sleep 60; done
[ -z "$G" ] && { echo "[$V] skipped: no GPU with room after 60 min"; exit 0; }
echo "[$V] $(date) train on GPUs $G"
GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_$V.yaml > logs/train_9b_$V.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[$V] TRAIN FAILED: $(grep -iE 'out of memory|Error|Traceback' logs/train_9b_$V.log | grep -v errors | tail -1 | cut -c1-190)"; exit 0; }
echo "[$V] train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-130)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_$V.yaml
scripts/lf_export.sh configs/lf/export_9b_$V.yaml > logs/export_9b_$V.log 2>&1 || { echo "[$V] EXPORT FAILED"; exit 0; }
G1=""; for _ in $(seq 1 40); do G1=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G1" ] && break; sleep 30; done
[ -z "$G1" ] && { echo "[$V] eval skipped: no GPU with 40 GB free"; exit 0; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G1
OUT=eval/out/q9b_mm1_img_text_bench; mkdir -p $OUT
# the SAME prompts and the SAME budget mm_img_text was scored with, so 12.7's cross-harness floor does not apply
python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4text_bench_prompts.jsonl --out $OUT \
  --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -1
N=$(ls $OUT | grep -vc extract_stats.json || true)
[ "${N:-0}" -lt 5 ] && { echo "[$V] GENERATION PRODUCED NOTHING — not scoring"; exit 0; }
conda activate llmft
python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
python - "$OUT" <<'PY'
import json, sys
r=[json.loads(l) for l in open(f"{sys.argv[1]}/gens_shard0.jsonl")]
print(f"[mm1]   unfinished={sum(1 for x in r if not x.get('finished',True))}/{len(r)}  mean_tok={sum(x.get('n_new_tokens',0) for x in r)/len(r):.0f}")
PY
echo "[$V] DONE"
