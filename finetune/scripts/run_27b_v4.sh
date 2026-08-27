#!/bin/bash
# 27B v4: v2's winning blender-heavy core + CadQuery raised 33k -> 105k unique samples (one caption each).
# Question: can a single 27B top both 3DCodeBench (v2: 93.4%) and CadQuery (9B md_xl: 97.5%)?
#
# 27B LoRA on 80 GB cards sits right at the memory edge — the identical recipe that trained v2 OOMs on other
# days. So instead of guessing, try a ladder of settings (keeping lora_rank 64, the quality-relevant knob;
# every sample in this mix is <=3400 tokens, so a smaller cutoff truncates nothing) and take the first that
# survives 4 real steps.
set -uo pipefail
cd /wekafs/ict/hx_624/llm-ft
GPUS_USE=$(scripts/free_gpus.sh); NGPU=$(echo "$GPUS_USE" | awk -F, '{print NF}')
[ -z "$GPUS_USE" ] && { echo "[27bv4] no free GPUs"; exit 1; }
RUN=runs/lf_qwen38_27b_lora_v4

WIN=""
for SPEC in "3584 5 adamw_torch" "3072 6 adamw_torch" "3072 6 adamw_8bit" "2560 8 adamw_8bit"; do
  set -- $SPEC; CUT=$1; ACC=$2; OPT=$3
  echo "[27bv4] $(date) probing cutoff=$CUT accum=$ACC optim=$OPT"
  EXTRA=""; [ "$OPT" = "adamw_8bit" ] && EXTRA="optim=adamw_8bit"
  GPUS=$GPUS_USE scripts/lf_train.sh configs/lf/lora_27b_v4.yaml max_steps=4 save_steps=100000 eval_steps=100000 \
    cutoff_len=$CUT gradient_accumulation_steps=$ACC $EXTRA \
    tokenized_path=/wekafs/ict/hx_624/llm-ft/data/lf/tok_v4_$CUT \
    output_dir=/wekafs/ict/hx_624/llm-ft/runs/_probe_v4 > logs/probe_v4_$CUT$OPT.log 2>&1
  rm -rf runs/_probe_v4
  if grep -q "train_runtime" logs/probe_v4_$CUT$OPT.log; then
    S=$(grep -oE "[0-9]/4 \[[0-9:]+<[0-9:]+, *[0-9.]+s/it" logs/probe_v4_$CUT$OPT.log | tail -1)
    echo "[27bv4] OK at cutoff=$CUT accum=$ACC optim=$OPT ($S)"; WIN="$CUT $ACC $OPT"; break
  fi
  ERR=$(grep -oE "torch.OutOfMemoryError[^\"]*|Triton Error \[CUDA\]: out of memory|RuntimeError: [^\"]{0,120}" logs/probe_v4_$CUT$OPT.log | tail -1)
  echo "[27bv4] FAILED at cutoff=$CUT accum=$ACC optim=$OPT -> ${ERR:-unknown error, see logs/probe_v4_$CUT$OPT.log}"
  case "$ERR" in *"Triton >= 3.4"*|*"tilelang"*) echo "[27bv4] ABORT: the lf env's triton was downgraded (Hopper needs 3.7.1) — fix the env, not the config"; exit 2;; esac
done
[ -z "$WIN" ] && { echo "[27bv4] every setting OOMed — needs more GPUs or a sharded optimizer"; exit 1; }
set -- $WIN; CUT=$1; ACC=$2; OPT=$3
EXTRA=""; [ "$OPT" = "adamw_8bit" ] && EXTRA="optim=adamw_8bit"

echo "[27bv4] $(date) train (172.6k pairs / 114M tok, cutoff=$CUT accum=$ACC optim=$OPT, GPUs $GPUS_USE)"
GPUS=$GPUS_USE scripts/lf_train.sh configs/lf/lora_27b_v4.yaml cutoff_len=$CUT gradient_accumulation_steps=$ACC $EXTRA \
  tokenized_path=/wekafs/ict/hx_624/llm-ft/data/lf/tok_v4_$CUT > logs/train_27b_v4.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[27bv4] TRAIN FAILED: $(grep -E 'out of memory|Error' logs/train_27b_v4.log | grep -v errors | tail -1 | cut -c1-160)"; exit 1; }
echo "[27bv4] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"

sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.8-27B|" \
    -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" \
    -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_v4.yaml
scripts/lf_export.sh configs/lf/export_27b_v4.yaml > logs/export_27b_v4.log 2>&1 || { echo "[27bv4] EXPORT FAILED"; exit 1; }
echo "[27bv4] $(date) eval greedy"
GPUS=$GPUS_USE TP=2 eval/eval_all_run2.sh $RUN/merged q27b_v4 --max_new 8192 2>&1 | grep -E "gen-multi|status counts|dialect|DONE" | tail -20
echo "[27bv4] $(date) eval sampled (openscad/glsl)"
GPUS=$GPUS_USE TP=2 eval/eval_all_run2.sh $RUN/merged q27b_v4_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "dialect|DONE" | tail -4
echo "[27bv4] ALL DONE"
