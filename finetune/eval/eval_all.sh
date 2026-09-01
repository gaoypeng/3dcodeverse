#!/bin/bash
# One command to evaluate ANY model on everything: 3DCodeBench (Blender exec + geometry vs GT) and the five
# held-out dialect sets (CadQuery / OpenSCAD / GLSL / Blender / three.js), with dialect-aware code extraction.
#
#   GPUS=0 TP=1 eval/eval_all.sh <model_dir> <name> [--think] [--temp 0.7] [--seed 1] [--max_new 8192] [--suites ...]
#
# --think            keep the model's reasoning on (default: enable_thinking=false)
# --temp/--seed      sampling (default greedy)
# --suites           space-separated subset of: bench cadquery openscad glsl blender threejs   (default: all)
# results: eval/out/<name>_<suite>/summary.json  + eval/out/<name>_report.json (one row per suite)
set -uo pipefail
MODEL=$(readlink -f "$1"); NAME=$2; shift 2
GPU=${GPUS:-0}; TP=${TP:-1}; THINK=""; TEMP=0.0; SEED=0; MAXNEW=32768
SUITES="bench cadquery openscad glsl blender threejs"
while [ $# -gt 0 ]; do
  case "$1" in
    --think) THINK="1"; shift;;
    --temp) TEMP=$2; shift 2;;
    --seed) SEED=$2; shift 2;;
    --max_new) MAXNEW=$2; shift 2;;
    --suites) shift; SUITES=""; while [ $# -gt 0 ] && [[ "$1" != --* ]]; do SUITES="$SUITES $1"; shift; done;;
    *) echo "unknown arg $1"; exit 2;;
  esac
done
cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING
export PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright CUDA_VISIBLE_DEVICES=$GPU
NOTHINK="--no_think"; [ -n "$THINK" ] && NOTHINK=""
MAXLEN=$((MAXNEW + 4096))
echo "[eval_all] $(date) model=$MODEL name=$NAME gpus=$GPU tp=$TP think=${THINK:-0} temp=$TEMP suites=$SUITES"

# ---------- generation: ONE engine for all suites (loading a 27B per suite costs more than generating) ----------
conda activate vllm
SPEC=$(mktemp /tmp/hx_624_evalspec_XXXX.json)
python - "$NAME" "$SUITES" "$SPEC" <<'PY'
import json, sys, os
name, suites, spec_path = sys.argv[1], sys.argv[2].split(), sys.argv[3]
jobs = []
for s in suites:
    out = f"eval/out/{name}_{s}"; os.makedirs(out, exist_ok=True)
    if s == "bench":
        prompts, dia = "data/sft_v1/bench_prompts.jsonl", "blender"
    elif s == "threejs":
        prompts, dia = "eval/out/_3js_prompts.jsonl", "threejs"
    else:
        prompts, dia = f"{out}/prompts.jsonl", s
        with open(prompts, "w") as f:
            for l in open(f"data/multidialect/{s}/test.jsonl"):
                r = json.loads(l); f.write(json.dumps({"task": r["id"].replace("/", "__"), "messages": r["messages"][:2]}) + "\n")
    jobs.append({"name": s, "prompts": prompts, "out": out, "dialect": dia})
json.dump(jobs, open(spec_path, "w"))
print("[eval_all] suites:", [j["name"] for j in jobs])
PY
python eval/generate_multi.py --model $MODEL --spec @$SPEC --tp $TP $NOTHINK \
    --max_new_tokens $MAXNEW --max_model_len $MAXLEN --temperature $TEMP --seed $SEED 2>&1 \
    | grep -E "gen-multi|extract\]|Error|Traceback" | tail -40
rm -f $SPEC

# ---------- execution + metrics (llmft env) ----------
conda activate llmft
for S in $SUITES; do
  OUT=eval/out/${NAME}_${S}
  echo "[eval_all] exec $S"
  case $S in
    bench)    python eval/run_bench.py --gen_dir $OUT --workers 32 --timeout 300 2>&1 | grep -E "status counts" | tail -1
              python eval/metrics.py --gen_dir $OUT 2>&1 | tail -1;;
    blender)  python eval/blender_dialect_eval.py --test data/multidialect/blender/test.jsonl --gen_dir $OUT --workers 32 2>&1 | tail -1;;
    threejs)  python eval/threejs_eval.py --gen_dir $OUT 2>&1 | tail -2;;
    *)        python eval/dialect_eval.py --dialect $S --test data/multidialect/$S/test.jsonl --gen_dir $OUT --workers 32 2>&1 | tail -1;;
  esac
done
python eval/results_all.py $NAME
echo "[eval_all] DONE $NAME"
