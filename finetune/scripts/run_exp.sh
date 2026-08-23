#!/bin/bash
# run_exp.sh <GPU(s)> <NAME>: train LoRA (configs/lf/lora_<NAME>.yaml) -> merge -> vLLM generation (GPU) ; Blender exec + metrics run in the background (CPU)
set -uo pipefail
GPU=$1; NAME=$2
NGPU=$(echo $GPU | tr ',' '\n' | wc -l); ACC=$((8 / NGPU))
cd /wekafs/ict/hx_624/llm-ft
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_$NAME; OUT=/wekafs/ict/hx_624/llm-ft/eval/out/qwen35_9b_lora_$NAME
echo "[exp $NAME] $(date) train on GPU(s) $GPU (grad_accum=$ACC)"
GPUS=$GPU scripts/lf_train.sh configs/lf/lora_$NAME.yaml gradient_accumulation_steps=$ACC > logs/train_$NAME.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[exp $NAME] TRAIN FAILED"; exit 1; }
echo "[exp $NAME] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|adapter_name_or_path:.*|adapter_name_or_path: $RUN|" -e "s|export_dir:.*|export_dir: $RUN/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$NAME.yaml
scripts/lf_export.sh configs/lf/export_$NAME.yaml > logs/export_$NAME.log 2>&1 || { echo "[exp $NAME] EXPORT FAILED"; exit 1; }
echo "[exp $NAME] $(date) merged"
# --- generation on GPU (vLLM), then free the GPU
( source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm; export HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING TOKENIZERS_PARALLELISM=false CUDA_VISIBLE_DEVICES=$GPU
  mkdir -p $OUT; python eval/generate_vllm.py --model $RUN/merged --prompts data/sft_v1/bench_prompts.jsonl --out $OUT --tp $NGPU --no_think --max_new_tokens 6144 2>&1 | grep -E "gen-vllm|Error|Traceback" | tail -5 ) > logs/eval_$NAME.log 2>&1
cp $OUT/gens_shard0.jsonl $OUT/gens.jsonl 2>/dev/null || { echo "[exp $NAME] GEN FAILED"; cat logs/eval_$NAME.log | tail -5; exit 1; }
echo "[exp $NAME] $(date) gen done: $(grep gen-vllm logs/eval_$NAME.log | tail -1)"
rm -rf $RUN/merged
# --- Blender exec + metrics in background (CPU only) so the GPU queue can continue
nohup bash -c "source /wekafs/ict/hx_624/llm-ft/env.sh; cd /wekafs/ict/hx_624/llm-ft; python eval/run_bench.py --gen_dir $OUT --workers 32 --timeout 300 > $OUT/exec.log 2>&1; python eval/metrics.py --gen_dir $OUT > $OUT/metrics.log 2>&1; echo \"[exp $NAME] \$(date) eval done\"; cat $OUT/summary.json; echo \"[exp $NAME] DONE\"" >> logs/exp_$NAME.log 2>&1 &
echo "[exp $NAME] exec+metrics running in background"
