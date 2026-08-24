#!/bin/bash
# Execute + score already-generated suites (no GPU): eval/exec_suites.sh <name> [suites...]
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
NAME=$1; shift; SUITES=${@:-bench cadquery openscad glsl blender threejs}
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
export PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright
for S in $SUITES; do
  OUT=eval/out/${NAME}_${S}; [ -d $OUT ] || continue
  echo "[exec] $NAME/$S"
  case $S in
    bench)    python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep -E "status counts" | tail -1
              python eval/metrics.py --gen_dir $OUT 2>&1 | tail -1;;
    blender)  python eval/blender_dialect_eval.py --test data/multidialect/blender/test.jsonl --gen_dir $OUT --workers 24 2>&1 | tail -1;;
    threejs)  python eval/threejs_eval.py --gen_dir $OUT 2>&1 | tail -2;;
    *)        python eval/dialect_eval.py --dialect $S --test data/multidialect/$S/test.jsonl --gen_dir $OUT --workers 24 2>&1 | tail -1;;
  esac
done
python eval/results_all.py $NAME
echo "[exec] DONE $NAME"
