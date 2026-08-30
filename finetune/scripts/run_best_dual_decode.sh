#!/bin/bash
# Section 13 showed greedy understates every long-output dialect. The project's headline model was scored greedy
# only, so its OpenSCAD/GLSL/three.js numbers are very likely wrong in the same direction. Re-score it under both
# decodings so the report states what the best model can actually do, not what greedy lets it show.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_dpo_exec_v2
# the DPO scripts delete their merged output to save disk, so the best model has to be rebuilt from its chain:
# lora_v1/merged -> dpo_exec_v1/merged_r2 -> dpo_exec_v2/merged
if [ ! -d $RUN/merged ]; then
  if [ ! -d runs/lf_qwen35_9b_dpo_exec_v1/merged_r2 ]; then
    echo "[bestdual] rebuilding dpo_exec_v1/merged_r2"
    scripts/lf_export.sh configs/lf/export_dpo_exec_v1_r2.yaml > logs/export_best_r2.log 2>&1 \
      || { echo "[bestdual] STAGE1 EXPORT FAILED: $(grep -iE 'error|OSError' logs/export_best_r2.log | tail -1 | cut -c1-160)"; exit 0; }
  fi
  echo "[bestdual] rebuilding dpo_exec_v2/merged"
  scripts/lf_export.sh configs/lf/export_dpo_exec_v2.yaml > logs/export_best_dual.log 2>&1 \
    || { echo "[bestdual] STAGE2 EXPORT FAILED: $(grep -iE 'error|OSError' logs/export_best_dual.log | tail -1 | cut -c1-160)"; exit 0; }
fi
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[bestdual] no GPU with 40 GB free"; exit 0; }
echo "[bestdual] $(date) on GPU $G"
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_best_t07 --temp 0.7 --seed 1 2>&1 | tail -10
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
python - <<'PY'
import json, os
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
for label, n in [("dpo_exec_v2 greedy", "qwen35_9b_dpo_exec_v2"), ("dpo_exec_v2 T=0.7", "q9b_best_t07")]:
    p = f"eval/out/{n}_report.json"
    if not os.path.exists(p):
        s = f"eval/out/{n}/summary.json"
        if os.path.exists(s):
            d = json.load(open(s)); print(f"  {label:20s} bench only: {d['exec_ok']}/{d['n_tasks']} = {d['exec_ok_rate']*100:.1f}%")
        else: print(f"  {label:20s} (no report)")
        continue
    d = {x["suite"]: x for x in json.load(open(p))["suites"]}
    print(f"  {label:20s}" + "".join(f"{d[s]['exec_rate']*100:9.1f}%" if s in d else f"{'-':>10s}" for s in suites))
PY
echo "[bestdual] DONE"
