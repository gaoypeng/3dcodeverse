#!/bin/bash
# Same base, same hyper-parameters, only the pair source differs: mmmix2's pairs (transfer) vs md_9b_plus's own.
# Answers whether the 2.5 hours of per-model pair generation buys anything over reusing an existing set.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
BASE=$(readlink -f runs/lf_qwen35_9b_plus/merged)
for SPEC in "lf_qwen35_9b_plus_stop:q9b_plus_stop:transfer" "lf_qwen35_9b_plus_stopown:q9b_plus_stopown:own"; do
  IFS=: read -r RUNDIR NAME TAG <<< "$SPEC"
  RUN=runs/$RUNDIR
  for _ in $(seq 1 90); do [ -f $RUN/train_results.json ] && break; sleep 60; done
  [ -f $RUN/train_results.json ] || { echo "[pstop-$TAG] training never finished"; continue; }
  if [ ! -d $RUN/merged ]; then
    sed -e "s|model_name_or_path:.*|model_name_or_path: $BASE|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_$RUNDIR.yaml
    scripts/lf_export.sh configs/lf/export_$RUNDIR.yaml > logs/export_$RUNDIR.log 2>&1 || { echo "[pstop-$TAG] EXPORT FAILED"; continue; }
  fi
  G=""; for _ in $(seq 1 60); do G=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G" ] && break; sleep 30; done
  [ -z "$G" ] && { echo "[pstop-$TAG] no GPU"; continue; }
  echo "[pstop-$TAG] $(date) evaluating on GPU $G"
  GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged ${NAME}_greedy 2>&1 | tail -9
  GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged ${NAME}_t07 --temp 0.7 --seed 1 2>&1 | tail -9
  echo "[pstop-$TAG] DONE"
done
conda activate llmft
python - <<'PY'
import json, os
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
sets = [("md_9b_plus greedy", "q9b_plus_greedy"), ("md_9b_plus T=0.7", "q9b_plus_t07"),
        ("+transfer pairs greedy", "q9b_plus_stop_greedy"), ("+transfer pairs T=0.7", "q9b_plus_stop_t07"),
        ("+own pairs greedy", "q9b_plus_stopown_greedy"), ("+own pairs T=0.7", "q9b_plus_stopown_t07")]
for title, key in (("exec rate", "exec_rate"), ("mean output tokens", "mean_new_tokens")):
    print(f"  --- {title} ---")
    print(f"  {'model':26s}" + "".join(f"{s[:8]:>10s}" for s in suites))
    for label, n in sets:
        p = f"eval/out/{n}_report.json"
        if not os.path.exists(p): print(f"  {label:26s}  (missing)"); continue
        d = {x["suite"]: x for x in json.load(open(p))["suites"]}
        if key == "exec_rate":
            print(f"  {label:26s}" + "".join(f"{d[s][key]*100:9.1f}%" if s in d else f"{'-':>10s}" for s in suites))
        else:
            print(f"  {label:26s}" + "".join(f"{d[s].get(key,0):10,.0f}" if s in d else f"{'-':>10s}" for s in suites))
PY
echo "[pstop] ALL DONE"
