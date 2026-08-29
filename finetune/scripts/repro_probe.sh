#!/bin/bash
# 12.7 showed two DIFFERENT harnesses disagree on 29 of 211 tasks at temperature 0. The open question is whether
# greedy is reproducible when NOTHING changes: same script, same flags, same card. Three identical runs answer it.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING
M=runs/lf_qwen35_9b_mm_text/merged
G=""; for _ in $(seq 1 40); do G=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[repro] no GPU with 40 GB free"; exit 0; }
export CUDA_VISIBLE_DEVICES=$G
for R in 1 2 3; do
  OUT=eval/out/_repro_r$R; mkdir -p $OUT
  echo "[repro] run $R on GPU $G"
  python eval/generate_vllm.py --model $M --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp 1 \
    --no_think --dialect blender --max_new_tokens 32768 --max_model_len 40960 2>&1 | tail -1
done
conda activate llmft
python - <<'PY'
import os, glob, itertools
runs = ["eval/out/_repro_r1", "eval/out/_repro_r2", "eval/out/_repro_r3"]
tasks = sorted(set.intersection(*[{os.path.basename(p) for p in glob.glob(r + "/*") if os.path.isdir(p)} for r in runs]))
print(f"[repro] {len(tasks)} tasks common to all three runs")
for a, b in itertools.combinations(range(3), 2):
    same = sum(1 for t in tasks
               if open(f"{runs[a]}/{t}/code.py", "rb").read() == open(f"{runs[b]}/{t}/code.py", "rb").read())
    print(f"[repro] run{a+1} vs run{b+1}: byte-identical {same}/{len(tasks)}")
PY
echo "[repro] DONE"
