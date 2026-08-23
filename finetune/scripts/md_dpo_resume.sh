#!/bin/bash
# resume multi-dialect DPO after OOM: train with cutoff 4096 (long GLSL/OpenSCAD pairs), then export + evals
set -uo pipefail; GPU=${GPUS:-2,3}; G1=$(echo $GPU | cut -d, -f1); cd /wekafs/ict/hx_624/llm-ft
BASE=runs/lf_qwen35_9b_lora_md_mixed/merged; RUN=runs/lf_qwen35_9b_dpo_md_mixed
echo "[mddpo] $(date) retrain with cutoff 4096"
GPUS=$GPU scripts/lf_train.sh configs/lf/dpo_md_mixed.yaml gradient_accumulation_steps=8 cutoff_len=4096 > logs/train_dpo_md_mixed.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[mddpo] TRAIN FAILED"; grep -E "Error" logs/train_dpo_md_mixed.log | grep -v errors | tail -2 | cut -c1-200; exit 1; }
echo "[mddpo] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_dpo_md_mixed.yaml
scripts/lf_export.sh configs/lf/export_dpo_md_mixed.yaml > logs/export_dpo_md_mixed.log 2>&1 || { echo "[mddpo] EXPORT2 FAILED"; exit 1; }
GPUS=$G1 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_dpo_md_mixed --no_think --max_new_tokens 6144 > logs/eval_dpo_md_mixed.log 2>&1
echo "[mddpo] 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_dpo_md_mixed/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3))")"
GPUS=$G1 eval/run_dialect_eval.sh $RUN/merged dpo_md_mixed > logs/mdeval_dpo_md_mixed.log 2>&1
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm; export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G1
for D in glsl openscad cadquery; do OUT=eval/out/md_dpo_md_mixed_T07_$D; mkdir -p $OUT; cp eval/out/md_dpo_md_mixed_$D/prompts.jsonl $OUT/; python eval/generate_vllm.py --model $RUN/merged --prompts $OUT/prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 8192 --temperature 0.7 --seed 1 2>&1 | grep -E "gen-vllm" | tail -1; done
conda activate llmft
for D in glsl openscad cadquery; do python eval/dialect_eval.py --dialect $D --test data/multidialect/$D/test.jsonl --gen_dir eval/out/md_dpo_md_mixed_T07_$D --workers 32 2>&1 | grep -E "dialect"; done
for D in cadquery openscad glsl blender; do echo "[mddpo] greedy $D: $(cat eval/out/md_dpo_md_mixed_$D/summary.json | tr -d '\n ' | cut -c1-160)"; done
rm -rf $RUN/merged $BASE; echo "[mddpo] DONE"
