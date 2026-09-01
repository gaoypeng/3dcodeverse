#!/bin/bash
# Fine-tuning has been destroying two of the six dialects: the base 9B scores 46% on OpenSCAD and 77.5% on
# three.js without thinking, and every fine-tune so far lands at 2-6% and 32-52%. The training mix is why they
# were suspected -- OpenSCAD is 0.6% of it and three.js 0.2%, against CadQuery's 53.6% -- but every mix tried so
# far, large or small, gave the same 2-6%. So this runs the same balanced data at two learning rates: if only the
# ratio mattered, both recover; if drift from the base is what breaks them, only the low rate does.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
for TAG in lr1e4 lr3e5; do
  RUN=runs/lf_qwen35_9b_bal_$TAG
  G=""; for _ in $(seq 1 90); do G=${GPUS:-$(scripts/free_gpus.sh)}; [ -n "$G" ] && break; sleep 60; done
  [ -z "$G" ] && { echo "[bal-$TAG] skipped: no GPU with room after 90 min"; continue; }
  echo "[bal-$TAG] $(date) train on GPUs $G"
  GPUS=$G scripts/lf_train.sh configs/lf/lora_9b_bal_$TAG.yaml > logs/train_bal_$TAG.log 2>&1
  [ -f $RUN/train_results.json ] || { echo "[bal-$TAG] TRAIN FAILED: $(grep -iE 'out of memory|Error|Traceback' logs/train_bal_$TAG.log | grep -v errors | tail -1 | cut -c1-190)"; continue; }
  echo "[bal-$TAG] train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-130)"
  sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_bal_$TAG.yaml
  scripts/lf_export.sh configs/lf/export_bal_$TAG.yaml > logs/export_bal_$TAG.log 2>&1 || { echo "[bal-$TAG] EXPORT FAILED"; continue; }
  G1=""; for _ in $(seq 1 60); do G1=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G1" ] && break; sleep 30; done
  [ -z "$G1" ] && { echo "[bal-$TAG] eval skipped: no GPU with 40 GB free"; continue; }
  echo "[bal-$TAG] $(date) six suites on GPU $G1"
  # all six, because the point of this run is what happens to the dialects the earlier ones broke
  GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_bal_${TAG}_all 2>&1 | tail -12
  echo "[bal-$TAG] DONE"
done
python - <<'PY'
import json, os
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
names = [("base (no-think)", "q9b_nothink"), ("mmmix2", "q9b_mmmix2_all"),
         ("balanced lr1e-4", "q9b_bal_lr1e4_all"), ("balanced lr3e-5", "q9b_bal_lr3e5_all")]
print(f"  {'model':18s}" + "".join(f"{s[:8]:>9s}" for s in suites))
for label, n in names:
    p = f"eval/out/{n}_report.json"
    if not os.path.exists(p):
        print(f"  {label:18s}  (no report)"); continue
    d = {s["suite"]: s["exec_rate"] * 100 for s in json.load(open(p))["suites"]}
    print(f"  {label:18s}" + "".join(f"{d[s]:9.1f}" if s in d else f"{'-':>9s}" for s in suites))
PY
echo "[bal] ALL DONE"
