#!/bin/bash
# After md_big training: evaluate intermediate checkpoints (training-amount curve) on 3DCodeBench (greedy) + dialect tests (greedy blender; T=0.7 cq/scad/glsl)
set -uo pipefail; GPU=${GPUS:-1}; cd /wekafs/ict/hx_624/llm-ft; RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_md_big
until grep -q "\[md md_big\] .*train done" logs/exp_md_big.log 2>/dev/null; do sleep 60; done
CKS=$(ls -d $RUN/checkpoint-* 2>/dev/null | sed 's/.*checkpoint-//' | sort -n | tr '\n' ' '); echo "[bigsweep] checkpoints: $CKS"
for STEP in $CKS; do
  CK=$RUN/checkpoint-$STEP; NAME=md_big_ck$STEP
  sed -e "s|adapter_name_or_path:.*|adapter_name_or_path: $CK|" -e "s|export_dir:.*|export_dir: $CK/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$NAME.yaml
  scripts/lf_export.sh configs/lf/export_$NAME.yaml > logs/export_$NAME.log 2>&1 || { echo "[bigsweep] EXPORT FAILED $STEP"; continue; }
  GPUS=$GPU eval/run_eval_vllm.sh $CK/merged eval/out/qwen35_9b_lora_$NAME --no_think --max_new_tokens 6144 > logs/eval_$NAME.log 2>&1
  echo "[bigsweep] ck$STEP 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_lora_$NAME/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3))")"
  GPUS=$GPU eval/run_dialect_eval.sh $CK/merged $NAME blender > logs/mdeval_$NAME.log 2>&1
  source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm; export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$GPU
  for D in glsl openscad cadquery; do OUT=eval/out/md_${NAME}_T07_$D; mkdir -p $OUT; cp eval/out/md_md_mixed_$D/prompts.jsonl $OUT/; python eval/generate_vllm.py --model $CK/merged --prompts $OUT/prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 8192 --temperature 0.7 --seed 1 2>&1 | grep -E "gen-vllm" | tail -1; done
  conda activate llmft
  for D in glsl openscad cadquery; do python eval/dialect_eval.py --dialect $D --test data/multidialect/$D/test.jsonl --gen_dir eval/out/md_${NAME}_T07_$D --workers 32 2>&1 | grep -E "dialect"; done
  echo "[bigsweep] ck$STEP blender: $(cat eval/out/md_${NAME}_blender/summary.json | tr -d '\n ' | cut -c1-140)"
  rm -rf $CK/merged
done
echo "[bigsweep] DONE"
