#!/bin/bash
# usage: GPUS=2 eval/run_dialect_eval.sh <model_dir> <name> [dialects...]   -> eval/out/md_<name>_<dialect>/summary.json
set -uo pipefail; MODEL=$(readlink -f "$1"); NAME=$2; shift 2; DIALECTS=${@:-cadquery openscad glsl blender}
cd /wekafs/ict/hx_624/llm-ft; source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=${GPUS:-0}
for D in $DIALECTS; do
  OUT=eval/out/md_${NAME}_$D; mkdir -p $OUT
  python - "$D" "$OUT" <<PY
import json,sys
D,OUT=sys.argv[1],sys.argv[2]
with open(f"{OUT}/prompts.jsonl","w") as f:
    for l in open(f"data/multidialect/{D}/test.jsonl"):
        r=json.loads(l); f.write(json.dumps({"task": r["id"].replace("/","__"), "messages": r["messages"][:2]})+"\n")
PY
  python eval/generate_vllm.py --model $MODEL --prompts $OUT/prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 8192 2>&1 | grep -E "gen-vllm|Error" | tail -2
done
conda activate llmft
for D in $DIALECTS; do
  OUT=eval/out/md_${NAME}_$D
  if [ "$D" = "blender" ]; then
    # blender held-out test: run in Blender and compare to reference via the same exec harness (reference meshes cached)
    python eval/blender_dialect_eval.py --test data/multidialect/blender/test.jsonl --gen_dir $OUT --workers 32 2>&1 | tail -2
  else
    python eval/dialect_eval.py --dialect $D --test data/multidialect/$D/test.jsonl --gen_dir $OUT --workers 32 2>&1 | tail -2
  fi
done
echo "[md-eval $NAME] DONE"
