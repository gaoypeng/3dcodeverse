#!/bin/bash
# 8h exploration scheduler: (1) after md_xl sweep + DPO finish -> md_bal LoRA on 4 GPUs (train+evals)
# (2) right after md_bal 'train done' -> full FT on the same md_bal data on GPUs 1,2,3 (ZeRO-3)  (3) after md_bal evals free GPU 0 -> geometry-feedback DPO train+eval on GPU 0
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
until grep -q "\[bigsweep\] DONE" logs/md_xl_ckpt_sweep.log 2>/dev/null; do sleep 60; done
until grep -qE "\[xldpo\] (DONE|TRAIN FAILED|EXPORT.* FAILED)" logs/md_xl_dpo_round.log 2>/dev/null; do sleep 60; done
echo "[sched] $(date) md_bal LoRA on GPUs 0,1,2,3"
scripts/run_md_exp.sh 0,1,2,3 md_bal > logs/exp_md_bal.log 2>&1 &
until grep -qE "\[md md_bal\] .*train done|TRAIN FAILED" logs/exp_md_bal.log 2>/dev/null; do sleep 60; done
sleep 60
echo "[sched] $(date) full FT md_bal on GPUs 1,2,3 (ZeRO-3, accum 3)"
( GPUS=1,2,3 scripts/lf_train.sh configs/lf/full_md_bal.yaml gradient_accumulation_steps=3 > logs/train_full_md_bal.log 2>&1
  RUN=runs/lf_qwen35_9b_full_md_bal; [ -f $RUN/train_results.json ] || { echo "[sched] FULL TRAIN FAILED"; exit 1; }
  echo "[sched] $(date) full md_bal train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
  until grep -q "\[md md_bal\] DONE" logs/exp_md_bal.log 2>/dev/null; do sleep 60; done   # wait until GPU 1 is surely free of md_bal evals? (evals run on GPU 0) -> use GPU 1
  GPUS=1 eval/run_eval_vllm.sh $RUN eval/out/qwen35_9b_full_md_bal --no_think --max_new_tokens 6144 > logs/eval_full_md_bal.log 2>&1
  echo "[sched] full md_bal 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_full_md_bal/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3))")"
  GPUS=1 eval/run_dialect_eval.sh $RUN full_md_bal > logs/mdeval_full_md_bal.log 2>&1
  for D in cadquery openscad glsl blender; do echo "[sched] full md_bal $D: $(cat eval/out/md_full_md_bal_$D/summary.json | tr -d '\n ' | cut -c1-160)"; done
  echo "[sched] FULL_DONE" ) &
until grep -q "\[md md_bal\] DONE" logs/exp_md_bal.log 2>/dev/null; do sleep 60; done
until grep -q "GEO_PAIRS_READY" logs/geo_dpo_build.log 2>/dev/null; do sleep 60; done
echo "[sched] $(date) geometry-feedback DPO on GPU 0"
GPUS=0 scripts/lf_train.sh configs/lf/dpo_geo_md_xl.yaml gradient_accumulation_steps=8 cutoff_len=4096 > logs/train_dpo_geo_md_xl.log 2>&1
RUN=runs/lf_qwen35_9b_dpo_geo_md_xl; [ -f $RUN/train_results.json ] || { echo "[sched] GEO DPO TRAIN FAILED"; grep -E "Error" logs/train_dpo_geo_md_xl.log | grep -v errors | tail -2 | cut -c1-200; }
if [ -f $RUN/train_results.json ]; then
  echo "[sched] $(date) geo dpo train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-200)"
  BASE=runs/lf_qwen35_9b_lora_md_xl/merged_geo
  sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_dpo_geo_md_xl.yaml
  scripts/lf_export.sh configs/lf/export_dpo_geo_md_xl.yaml > logs/export_dpo_geo_md_xl.log 2>&1
  GPUS=0 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_dpo_geo_md_xl --no_think --max_new_tokens 6144 > logs/eval_dpo_geo_md_xl.log 2>&1
  echo "[sched] geo dpo 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_dpo_geo_md_xl/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3), 'scored', round(s['f@0.05_mean_scored'],3))")"
  GPUS=0 eval/run_dialect_eval.sh $RUN/merged dpo_geo_md_xl blender cadquery > logs/mdeval_dpo_geo_md_xl.log 2>&1
  for D in blender cadquery; do echo "[sched] geo dpo $D: $(cat eval/out/md_dpo_geo_md_xl_$D/summary.json | tr -d '\n ' | cut -c1-200)"; done
  GPUS=0 eval/best_of_n.sh $RUN/merged dpo_geo_md_xl_T07 4 0.7 2>&1 | grep -E "pass@" | tail -1 | sed 's/^/[sched] geo dpo /'
fi
echo "[sched] GEO_DONE"
wait; echo "[sched] ALL DONE"
