#!/bin/bash
# mm_img scores 36.8% while writing 15,445 tokens per task and leaving 97 of 212 unfinished. Is that a capability
# gap, or the same non-termination pathology the three.js LoRA had (where T=0.7 recovered 30% -> 50%)?
# Four decoding settings on the SAME model answer it. If a decoding change alone moves the score, the model can
# build these objects and simply does not know when to stop.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
RUN=runs/lf_qwen35_9b_mm_img/merged
[ -d "$RUN" ] || { echo "[imgdec] no merged model"; exit 0; }
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[imgdec] no GPU with 40 GB free"; exit 0; }
run () {  # name, extra flags
  local NAME=$1; shift
  local OUT=eval/out/q9b_mm_img_dec_$NAME; mkdir -p $OUT
  echo "[imgdec] $NAME: $* "
  conda activate vllm
  export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
  python eval/generate_vllm_img.py --model $RUN --prompts eval/out/_img4_bench_prompts.jsonl --out $OUT \
    --max_new_tokens 32768 --max_model_len 40960 "$@" 2>&1 | tail -1
  local N=$(ls $OUT | grep -vc extract_stats.json || true)
  [ "${N:-0}" -lt 5 ] && { echo "[imgdec] $NAME PRODUCED NOTHING — not scoring"; return; }
  conda activate llmft
  python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
  python - "$OUT" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(f"{sys.argv[1]}/gens_shard0.jsonl")]
tr = sum(1 for r in rows if not r.get("finished", True))
print(f"[imgdec]   unfinished={tr}/{len(rows)}  mean_tok={sum(r.get('n_new_tokens',0) for r in rows)/len(rows):.0f}")
PY
}
# every setting is compared against the SAME harness, so 12.7's cross-harness noise floor does not apply here
run greedy
run temp07     --temperature 0.7 --seed 1
run presence   --presence_penalty 0.5
run reppen     --repetition_penalty 1.05
echo "[imgdec] ALL DONE"
