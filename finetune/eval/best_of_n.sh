#!/bin/bash
# usage: GPUS=0 eval/best_of_n.sh <model_dir> <out_prefix> <N> [temperature]
# N sampled generations (different seeds) -> exec each -> pass@N (union) via eval/pass_at_n.py
set -uo pipefail
MODEL=$(readlink -f "$1"); PFX=$2; N=$3; T=${4:-0.7}
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=${GPUS:-0}
cd /wekafs/ict/hx_624/llm-ft
for i in $(seq 1 $N); do
  OUT=eval/out/${PFX}_s$i; mkdir -p $OUT
  python eval/generate_vllm.py --model $MODEL --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 6144 --temperature $T --seed $i 2>&1 | grep -E "gen-vllm|Error" | tail -2
  cp $OUT/gens_shard0.jsonl $OUT/gens.jsonl
done
conda activate llmft
for i in $(seq 1 $N); do OUT=eval/out/${PFX}_s$i; python eval/run_bench.py --gen_dir $OUT --workers 32 --timeout 300 > $OUT/exec.log 2>&1; python eval/metrics.py --gen_dir $OUT > $OUT/metrics.log 2>&1; grep "status counts" $OUT/exec.log; done
python eval/pass_at_n.py $PFX $N
echo BEST_OF_N_DONE
