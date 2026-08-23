#!/bin/bash
# ckpt_sweep.sh <GPU> <RUN_DIR> <NAME> <ckpt_step> [<ckpt_step> ...]: merge each intermediate adapter checkpoint, vLLM-generate, exec in background
GPU=$1; RUN=$(readlink -f "$2"); NAME=$3; shift 3
cd /wekafs/ict/hx_624/llm-ft
for STEP in "$@"; do
  CK=$RUN/checkpoint-$STEP; [ -d $CK ] || { echo "[ckpt $NAME-$STEP] missing"; continue; }
  OUT=eval/out/qwen35_9b_lora_${NAME}_ckpt$STEP
  sed -e "s|adapter_name_or_path:.*|adapter_name_or_path: $CK|" -e "s|export_dir:.*|export_dir: $CK/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_${NAME}_ckpt$STEP.yaml
  scripts/lf_export.sh configs/lf/export_${NAME}_ckpt$STEP.yaml > logs/export_${NAME}_ckpt$STEP.log 2>&1 || { echo "[ckpt $NAME-$STEP] EXPORT FAILED"; continue; }
  ( source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm; export HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$GPU
    mkdir -p $OUT; python eval/generate_vllm.py --model $CK/merged --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 6144 2>&1 | grep -E "gen-vllm|Error|Traceback" | tail -3 ) > logs/eval_${NAME}_ckpt$STEP.log 2>&1
  cp $OUT/gens_shard0.jsonl $OUT/gens.jsonl; rm -rf $CK/merged
  echo "[ckpt $NAME-$STEP] gen done: $(grep gen-vllm logs/eval_${NAME}_ckpt$STEP.log | tail -1)"
  nohup bash -c "source /wekafs/ict/hx_624/llm-ft/env.sh; cd /wekafs/ict/hx_624/llm-ft; python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 > $OUT/exec.log 2>&1; python eval/metrics.py --gen_dir $OUT > $OUT/metrics.log 2>&1; echo \"[ckpt $NAME-$STEP] eval done\"; cat $OUT/summary.json" >> logs/ckpt_sweep_$NAME.log 2>&1 &
done
echo "CKPT_SWEEP_DONE $NAME"
