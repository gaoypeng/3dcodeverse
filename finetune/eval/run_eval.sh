#!/bin/bash
# End-to-end 3DCodeBench eval: generate (sharded over GPUS) -> execute in Blender -> geometry metrics
# usage: GPUS=0,1 ./run_eval.sh <model_path> <out_dir> [extra generate.py args]
set -euo pipefail
source /wekafs/ict/hx_624/llm-ft/env.sh
MODEL=$(readlink -f "$1"); OUT=$(readlink -m "$2"); shift 2
GPUS=${GPUS:-0}
PROMPTS=/wekafs/ict/hx_624/llm-ft/data/sft_v1/bench_prompts.jsonl
[ -f $PROMPTS ] || PROMPTS=/wekafs/ict/hx_624/llm-ft/data/sft_v0/bench_prompts.jsonl
mkdir -p $OUT
IFS=',' read -ra G <<< "$GPUS"; N=${#G[@]}
echo "[eval] model=$MODEL out=$OUT gpus=$GPUS shards=$N"
pids=()
for i in "${!G[@]}"; do
  CUDA_VISIBLE_DEVICES=${G[$i]} python /wekafs/ict/hx_624/llm-ft/eval/generate.py --model $MODEL --prompts $PROMPTS --out $OUT --shard $i --num_shards $N "$@" > $OUT/gen_shard$i.log 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait $p; done
cat $OUT/gens_shard*.jsonl > $OUT/gens.jsonl
echo "[eval] generation done: $(wc -l < $OUT/gens.jsonl) samples"
python /wekafs/ict/hx_624/llm-ft/eval/run_bench.py --gen_dir $OUT --workers ${WORKERS:-32} --timeout ${TIMEOUT:-300} 2>&1 | tee $OUT/exec.log
python /wekafs/ict/hx_624/llm-ft/eval/metrics.py --gen_dir $OUT 2>&1 | tee $OUT/metrics.log
