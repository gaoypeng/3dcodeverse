#!/bin/bash
# Re-run the image-conditioned evaluations with the model's real limit instead of an arbitrary cap.
# Both models support 262,144 tokens of context and the prompts are ~300 tokens, so nothing but vLLM's KV-cache
# budget limits the output. Probe downward from the model maximum and use the largest length that initialises;
# report the truncation count, which must be 0 for the score to mean anything.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
G=$(scripts/free_gpus.sh | cut -d, -f1)
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G

RUN=runs/lf_qwen35_9b_img4
LEN=0
for L in 262144 131072 65536 32768; do
  echo "[imgbudget] probing max_model_len=$L"
  if timeout 1200 python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4_bench_prompts.jsonl \
       --out /tmp/hx_budget_probe --max_model_len $L --max_new_tokens $((L-2048)) --limit 2 > logs/probe_len_$L.log 2>&1; then
    LEN=$L; echo "[imgbudget] max_model_len=$L works"; break
  fi
  echo "[imgbudget] $L failed: $(grep -oE 'ValueError[^\"]{0,120}|out of memory|KV cache[^\"]{0,80}' logs/probe_len_$L.log | tail -1)"
done
[ "$LEN" -eq 0 ] && { echo "[imgbudget] no context length initialised"; exit 1; }
NEW=$((LEN-2048))
echo "[imgbudget] running the suites with max_model_len=$LEN max_new_tokens=$NEW"
for SPEC in "_img4_bench_prompts q9b_img4_full" "_img_bench_prompts q9b_img1_full"; do
  set -- $SPEC; OUT=eval/out/$2; mkdir -p $OUT
  python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/$1.jsonl --out $OUT \
    --max_model_len $LEN --max_new_tokens $NEW 2>&1 | tail -1
done
conda activate llmft
for O in q9b_img4_full q9b_img1_full; do
  echo "== $O"
  python3 -c "
import json,statistics as st
g=[json.loads(l) for l in open('eval/out/$O/gens_shard0.jsonl')]
print('   mean out tok %.0f | truncated %d/%d' % (st.mean(r['n_new_tokens'] for r in g), sum(1 for r in g if not r['finished']), len(g)))"
  python eval/run_bench.py --gen_dir eval/out/$O --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
  python eval/metrics.py --gen_dir eval/out/$O 2>&1 | tail -1
done
echo "[imgbudget] ALL DONE"
