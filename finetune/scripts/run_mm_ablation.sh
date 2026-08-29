#!/bin/bash
# Three-way ablation on identical samples and an identical val split: image only vs image+instruction vs text only.
# Answers what a reference render is actually worth on top of the caption we already have.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
for V in img_text img text; do
  G=""; for _ in $(seq 1 60); do G=$(scripts/free_gpus.sh); [ -n "$G" ] && break; sleep 60; done
  [ -z "$G" ] && { echo "[mm-$V] skipped: no GPU with enough free memory after 60 min"; continue; }
  RUN=runs/lf_qwen35_9b_mm_$V
  echo "[mm-$V] $(date) train on GPUs $G"
  GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_mm_$V.yaml > logs/train_mm_$V.log 2>&1
  if [ ! -f $RUN/train_results.json ]; then
    echo "[mm-$V] TRAIN FAILED: $(grep -iE 'out of memory|error' logs/train_mm_$V.log | grep -v errors | tail -1 | cut -c1-160)"; continue; fi
  echo "[mm-$V] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-130)"
  sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_mm_$V.yaml
  scripts/lf_export.sh configs/lf/export_mm_$V.yaml > logs/export_mm_$V.log 2>&1 || { echo "[mm-$V] EXPORT FAILED"; continue; }
  # a trainer takes a while to hand its memory back; wait for it instead of failing the eval
  G1=""; for _ in $(seq 1 20); do G1=$(scripts/free_gpus.sh 30000 | cut -d, -f1); [ -n "$G1" ] && break; sleep 30; done
  [ -z "$G1" ] && { echo "[mm-$V] eval skipped: no GPU with 30 GB free after 10 min — $(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader -i 0,1,2,3 | tr '\n' ' ')"; continue; }
  source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
  export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G1
  OUT=eval/out/q9b_mm_${V}_bench; mkdir -p $OUT
  # every variant is evaluated on the SAME 212 tasks, each fed the input its training used
  case $V in
    text)     python eval/generate_vllm.py --model $RUN/merged --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp 1 --no_think --dialect blender --max_new_tokens 32768 2>&1 | tail -1;;
    img)      python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4_bench_prompts.jsonl --out $OUT --max_new_tokens 32768 2>&1 | tail -1;;
    img_text) python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img4text_bench_prompts.jsonl --out $OUT --max_new_tokens 32768 2>&1 | tail -1;;
  esac
  conda activate llmft
  python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
  python eval/metrics.py --gen_dir $OUT 2>&1 | tail -1
  echo "[mm-$V] EVAL DONE"
done
echo "[mm] ALL DONE"
