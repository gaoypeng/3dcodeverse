#!/bin/bash
# mmmix2 mixes image+text, image-only and the full text corpus into one model. It trained but was never scored.
# Two questions: does mixing image data cost the TEXT path anything (compare against md_9b_noleak, 84.9%), and
# does one model handle both input types? Text suites and image paths are all run here so the numbers are
# comparable within this one harness (12.7).
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_mmmix2/merged
[ -d "$RUN" ] || { echo "[mmmix2eval] no merged model"; exit 0; }
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[mmmix2eval] no GPU with 40 GB free after 30 min"; exit 0; }
echo "[mmmix2eval] $(date) six text suites on GPU $G"
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN q9b_mmmix2_all 2>&1 | tail -20
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
for SPEC in "_img4text_bench_prompts img_text" "_img4_bench_prompts img"; do
  set -- $SPEC
  OUT=eval/out/q9b_mmmix2_$2; mkdir -p $OUT
  echo "[mmmix2eval] image path: $2"
  conda activate vllm
  export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
  python eval/generate_vllm_img.py --model $RUN --prompts eval/out/$1.jsonl --out $OUT \
    --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -1
  N=$(ls $OUT | grep -vc extract_stats.json || true)
  [ "${N:-0}" -lt 5 ] && { echo "[mmmix2eval] $2 PRODUCED NOTHING — not scoring"; continue; }
  conda activate llmft
  python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
  python - "$OUT" <<'PY'
import json, sys
r=[json.loads(l) for l in open(f"{sys.argv[1]}/gens_shard0.jsonl")]
print(f"[mmmix2eval]   unfinished={sum(1 for x in r if not x.get('finished',True))}/{len(r)}  mean_tok={sum(x.get('n_new_tokens',0) for x in r)/len(r):.0f}")
PY
done
echo "[mmmix2eval] ALL DONE"
