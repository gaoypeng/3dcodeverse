#!/bin/bash
# 3DCodeVerse evaluation: one command scores ANY model by EXECUTING what it generates -- 3DCodeBench
# (Blender exec + geometry against ground truth) and five held-out dialect sets (CadQuery / OpenSCAD /
# GLSL / Blender / three.js), with dialect-aware code extraction.
#
#   GPUS=0 TP=1 ./run_eval.sh <model_dir> <name> [--think] [--temp 0.7] [--seed 1] [--max_new 32768] [--suites ...]
#
# --think        keep the model's reasoning on (default: enable_thinking=false)
# --temp/--seed  sampling (default greedy). Report long-output dialects under BOTH -- greedy alone
#                understates them badly when a model runs away.
# --suites       subset of: bench cadquery openscad glsl blender threejs   (default: all)
#
# Paths are env vars so this folder runs on another box: PROJECT_ROOT (holds data/), EVAL_OUT,
# BLENDER_BIN, OPENSCAD_BIN, GLSLANG_BIN, BENCH_DATA, VLLM_ENV, EXEC_ENV. See env.sh and README.md.
# results: $EVAL_OUT/<name>_<suite>/summary.json + $EVAL_OUT/<name>_report.json (one row per suite)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-/wekafs/ict/hx_624/llm-ft}"
export EVAL_OUT="${EVAL_OUT:-$PROJECT_ROOT/eval/out}"
VLLM_ENV="${VLLM_ENV:-vllm}"; EXEC_ENV="${EXEC_ENV:-llmft}"
CONDA_SH="${CONDA_SH:-/wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh}"
mkdir -p "$EVAL_OUT"
MODEL=$(readlink -f "$1"); NAME=$2; shift 2
GPU=${GPUS:-0}; TP=${TP:-1}; THINK=""; TEMP=0.0; SEED=0; MAXNEW=32768   # the models take 262k context and our prompts are ~300 tokens; a low cap silently turns "unfinished" into "wrong"
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
cd "$PROJECT_ROOT"
source "$CONDA_SH"
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING
export PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright CUDA_VISIBLE_DEVICES=$GPU
NOTHINK="--no_think"; [ -n "$THINK" ] && NOTHINK=""
MAXLEN=$((MAXNEW + 4096))
echo "[eval_all] $(date) model=$MODEL name=$NAME gpus=$GPU tp=$TP think=${THINK:-0} temp=$TEMP suites=$SUITES"

# ---------- generation: ONE engine for all suites (loading a 27B per suite costs more than generating) ----------
conda activate "$VLLM_ENV"
SPEC=$(mktemp /tmp/hx_624_evalspec_XXXX.json)
python - "$NAME" "$SUITES" "$SPEC" <<'PY'
import json, sys, os
name, suites, spec_path = sys.argv[1], sys.argv[2].split(), sys.argv[3]
jobs = []
for s in suites:
    out = f"{os.environ['EVAL_OUT']}/{name}_{s}"; os.makedirs(out, exist_ok=True)
    if s == "bench":
        prompts, dia = "data/sft_v1/bench_prompts.jsonl", "blender"
    elif s == "threejs":
        prompts, dia = os.environ["EVAL_OUT"] + "/_3js_prompts.jsonl", "threejs"
    else:
        prompts, dia = f"{out}/prompts.jsonl", s
        with open(prompts, "w") as f:
            for l in open(f"data/multidialect/{s}/test.jsonl"):
                r = json.loads(l); f.write(json.dumps({"task": r["id"].replace("/", "__"), "messages": r["messages"][:2]}) + "\n")
    jobs.append({"name": s, "prompts": prompts, "out": out, "dialect": dia})
json.dump(jobs, open(spec_path, "w"))
print("[eval_all] suites:", [j["name"] for j in jobs])
PY
# On a shared box a card that was free when it was picked can be full by the time vLLM starts, and the engine
# dies with no generations at all. Re-check right before launching, and fall back to whichever card is free now.
for ATTEMPT in 1 2 3; do
  FREE_NOW=$("$HERE"/free_gpus.sh 40000)
  case ",$FREE_NOW," in
    *",$GPU,"*) ;;
    *) NEW=$(echo "$FREE_NOW" | cut -d, -f1)
       if [ -n "$NEW" ]; then
         echo "[eval_all] GPU $GPU filled up since it was picked; switching to $NEW"
         GPU=$NEW; export CUDA_VISIBLE_DEVICES=$GPU
       else
         echo "[eval_all] no card has 40 GB free; waiting 120s (attempt $ATTEMPT/3)"; sleep 120; continue
       fi;;
  esac
  python "$HERE"/generate_multi.py --model $MODEL --spec @$SPEC --tp $TP $NOTHINK \
      --max_new_tokens $MAXNEW --max_model_len $MAXLEN --temperature $TEMP --seed $SEED 2>&1 \
      | grep -E "gen-multi|extract\]|Error|Traceback" | tail -40
  # one generation directory per task; anything less means the engine never ran
  if [ "$(find $EVAL_OUT/${NAME}_$(echo $SUITES | awk '{print $1}') -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)" -ge 5 ]; then break; fi
  echo "[eval_all] generation produced nothing on attempt $ATTEMPT; retrying"
  sleep 60
done
rm -f $SPEC

# ---------- execution + metrics (llmft env) ----------
conda activate "$EXEC_ENV"
for S in $SUITES; do
  OUT=$EVAL_OUT/${NAME}_${S}
  # An engine that never started leaves an empty directory, and every scorer below reports that as 0%. A real
  # 0% and a failed launch then look identical -- which is how a rescore of two healthy models came back as
  # 0/200 on every suite. Refuse to score instead.
  NGEN=$(find $OUT -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
  if [ "${NGEN:-0}" -lt 5 ]; then
    echo "[eval_all] $S PRODUCED NOTHING ($NGEN generations) — not scoring. See the generation log above."
    continue
  fi
  echo "[eval_all] exec $S"
  case $S in
    bench)    python "$HERE"/run_bench.py --gen_dir $OUT --workers 32 --timeout 300 2>&1 | grep -E "status counts" | tail -1
              python "$HERE"/metrics.py --gen_dir $OUT 2>&1 | tail -1;;
    blender)  python "$HERE"/blender_dialect_eval.py --test data/multidialect/blender/test.jsonl --gen_dir $OUT --workers 32 2>&1 | tail -1;;
    threejs)  python "$HERE"/threejs_eval.py --gen_dir $OUT 2>&1 | tail -2;;
    *)        python "$HERE"/dialect_eval.py --dialect $S --test data/multidialect/$S/test.jsonl --gen_dir $OUT --workers 32 2>&1 | tail -1;;
  esac
done
python "$HERE"/results_all.py $NAME
echo "[eval_all] DONE $NAME"
