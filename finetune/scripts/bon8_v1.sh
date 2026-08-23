#!/bin/bash
# best-of-8 for LoRA-v1: 8 sampled generations (T=0.7) on 4 GPUs now; Blender exec deferred until round-2 exec finishes (CPU contention); then pass@n
cd /wekafs/ict/hx_624/llm-ft; PFX=qwen35_9b_lora_v1_T07
scripts/lf_export.sh configs/lf/export_v1.yaml > logs/export_v1_bon8.log 2>&1 || { echo "[bon8] EXPORT FAILED"; exit 1; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING
for round in 0 1; do
  for g in 0 1 2 3; do i=$((round*4+g+1)); [ $i -le 4 ] && continue   # samples 1-4 already exist
    OUT=eval/out/${PFX}_s$i; mkdir -p $OUT
    CUDA_VISIBLE_DEVICES=$g python eval/generate_vllm.py --model runs/lf_qwen35_9b_lora_v1/merged --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 6144 --temperature 0.7 --seed $i > $OUT/gen.log 2>&1 &
  done; wait
done
for i in 5 6 7 8; do cp eval/out/${PFX}_s$i/gens_shard0.jsonl eval/out/${PFX}_s$i/gens.jsonl; grep gen-vllm eval/out/${PFX}_s$i/gen.log | tail -1; done
rm -rf runs/lf_qwen35_9b_lora_v1/merged; echo "[bon8] $(date) gens done (s5-s8)"
until grep -q "\[r2\] .*exec done" logs/self_train_round2.log 2>/dev/null; do sleep 60; done
conda activate llmft
for i in 5 6 7 8; do OUT=eval/out/${PFX}_s$i; python eval/run_bench.py --gen_dir $OUT --workers 32 --timeout 300 > $OUT/exec.log 2>&1; python eval/metrics.py --gen_dir $OUT > $OUT/metrics.log 2>&1; grep "status counts" $OUT/exec.log; done
python eval/pass_at_n.py $PFX 8; python eval/pass_at_n.py $PFX 4; echo "[bon8] DONE"
