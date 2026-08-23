#!/bin/bash
# revised 8h scheduler (GPUs 0,2,3 idle now, GPU 1 busy with the md_xl sweep):
# (1) now: md_bal LoRA on GPUs 0,2,3 (train+merge+evals)   (2) after sweep: geometry-feedback DPO on GPU 1 (train+export+evals)
# (3) after both: full FT md_bal on GPUs 0,1,2,3 (ZeRO-3) + evals
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
echo "[sched] $(date) md_bal LoRA on GPUs 0,2,3"
scripts/run_md_exp.sh 0,2,3 md_bal > logs/exp_md_bal.log 2>&1 &
( until grep -q "\[bigsweep\] DONE" logs/md_xl_ckpt_sweep.log 2>/dev/null; do sleep 60; done
  echo "[sched] $(date) geometry-feedback DPO on GPU 1"
  GPUS=1 scripts/lf_train.sh configs/lf/dpo_geo_md_xl.yaml gradient_accumulation_steps=8 cutoff_len=4096 > logs/train_dpo_geo_md_xl.log 2>&1
  RUN=runs/lf_qwen35_9b_dpo_geo_md_xl
  if [ ! -f $RUN/train_results.json ]; then echo "[sched] GEO DPO TRAIN FAILED"; grep -E "Error" logs/train_dpo_geo_md_xl.log | grep -v errors | tail -2 | cut -c1-200; echo "[sched] GEO_DONE"; exit 0; fi
  echo "[sched] $(date) geo dpo train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
  BASE=runs/lf_qwen35_9b_lora_md_xl/merged_geo
  sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_dpo_geo_md_xl.yaml
  scripts/lf_export.sh configs/lf/export_dpo_geo_md_xl.yaml > logs/export_dpo_geo_md_xl.log 2>&1
  GPUS=1 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_dpo_geo_md_xl --no_think --max_new_tokens 6144 > logs/eval_dpo_geo_md_xl.log 2>&1
  echo "[sched] geo dpo 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_dpo_geo_md_xl/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3), 'scored', round(s['f@0.05_mean_scored'],3), 'f@0.1 scored', round(s['f@0.1_mean_scored'],3))")"
  GPUS=1 eval/run_dialect_eval.sh $RUN/merged dpo_geo_md_xl blender cadquery > logs/mdeval_dpo_geo_md_xl.log 2>&1
  for D in blender cadquery; do echo "[sched] geo dpo $D: $(cat eval/out/md_dpo_geo_md_xl_$D/summary.json | tr -d '\n ' | cut -c1-200)"; done
  GPUS=1 eval/best_of_n.sh $RUN/merged dpo_geo_md_xl_T07 4 0.7 2>&1 | grep -E "pass@" | tail -1 | sed 's/^/[sched] geo dpo /'
  echo "[sched] GEO_DONE" ) &
until grep -qE "\[md md_bal\] (DONE|TRAIN FAILED|EXPORT FAILED)" logs/exp_md_bal.log 2>/dev/null; do sleep 60; done
until grep -q "GEO_DONE" logs/explore_sched2.log 2>/dev/null; do sleep 60; done
echo "[sched] $(date) full FT md_bal on GPUs 0,1,2,3 (ZeRO-3, accum 2)"
GPUS=0,1,2,3 scripts/lf_train.sh configs/lf/full_md_bal.yaml gradient_accumulation_steps=2 > logs/train_full_md_bal.log 2>&1
RUN=runs/lf_qwen35_9b_full_md_bal
if [ -f $RUN/train_results.json ]; then
  echo "[sched] $(date) full md_bal train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
  GPUS=0 eval/run_eval_vllm.sh $RUN eval/out/qwen35_9b_full_md_bal --no_think --max_new_tokens 6144 > logs/eval_full_md_bal.log 2>&1 &
  GPUS=1 eval/run_dialect_eval.sh $RUN full_md_bal > logs/mdeval_full_md_bal.log 2>&1 &
  wait
  echo "[sched] full md_bal 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_full_md_bal/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3))")"
  for D in cadquery openscad glsl blender; do echo "[sched] full md_bal $D: $(cat eval/out/md_full_md_bal_$D/summary.json | tr -d '\n ' | cut -c1-160)"; done
else echo "[sched] FULL TRAIN FAILED"; grep -E "Error" logs/train_full_md_bal.log | grep -v errors | tail -2 | cut -c1-200; fi
echo "[sched] FULL_DONE"; echo "[sched] ALL DONE"
