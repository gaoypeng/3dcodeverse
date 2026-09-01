#!/bin/bash
# Train the runaway out of the 9B, then score it under BOTH decodings. Reporting only greedy is what hid this
# problem for the whole project: OpenSCAD read as 6% (a "forgotten dialect") when the same weights sampled give
# 44%, against a 46% base. From here every long-output dialect gets both numbers.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
[ -f data/lf/stop_dpo_pairs.json ] || { echo "[stoptrain] pairs not built yet"; exit 0; }
N=$(python3 -c "import json;print(len(json.load(open('data/lf/stop_dpo_pairs.json'))))")
[ "$N" -lt 100 ] && { echo "[stoptrain] only $N pairs — too few to train on"; exit 0; }
echo "[stoptrain] $(date) $N preference pairs"
RUN=runs/lf_qwen35_9b_stopdpo
G=""; for _ in $(seq 1 90); do G=${GPUS:-$(scripts/free_gpus.sh)}; [ -n "$G" ] && break; sleep 60; done
[ -z "$G" ] && { echo "[stoptrain] no GPU with room"; exit 0; }
echo "[stoptrain] train on GPUs $G"
GPUS=$G scripts/lf_train.sh configs/lf/dpo_stop.yaml > logs/train_stopdpo.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[stoptrain] TRAIN FAILED: $(grep -iE 'out of memory|Error|Traceback' logs/train_stopdpo.log | grep -v errors | tail -1 | cut -c1-190)"; exit 0; }
echo "[stoptrain] train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-130)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_mmmix2/merged|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_stopdpo.yaml
scripts/lf_export.sh configs/lf/export_stopdpo.yaml > logs/export_stopdpo.log 2>&1 || { echo "[stoptrain] EXPORT FAILED"; exit 0; }
G1=""; for _ in $(seq 1 60); do G1=$(scripts/free_gpus.sh 40000 | cut -d, -f1); [ -n "$G1" ] && break; sleep 30; done
[ -z "$G1" ] && { echo "[stoptrain] eval skipped: no GPU with 40 GB free"; exit 0; }
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_stopdpo_greedy 2>&1 | tail -10
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_stopdpo_t07 --temp 0.7 --seed 1 2>&1 | tail -10
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
python - <<'PY'
import json, os
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
sets = [("mmmix2 greedy", "q9b_mmmix2_all"), ("mmmix2 T=0.7", "q9b_mmmix2_t07"),
        ("stopDPO greedy", "q9b_stopdpo_greedy"), ("stopDPO T=0.7", "q9b_stopdpo_t07")]
print(f"  {'model':16s}" + "".join(f"{s[:8]:>10s}" for s in suites))
for label, n in sets:
    p = f"eval/out/{n}_report.json"
    if not os.path.exists(p): print(f"  {label:16s}  (missing)"); continue
    d = {s["suite"]: s for s in json.load(open(p))["suites"]}
    print(f"  {label:16s}" + "".join(f"{d[s]['exec_rate']*100:9.1f}%" if s in d else f"{'-':>10s}" for s in suites))
print("  mean output tokens (the behaviour being trained)")
for label, n in sets:
    p = f"eval/out/{n}_report.json"
    if not os.path.exists(p): continue
    d = {s["suite"]: s for s in json.load(open(p))["suites"]}
    print(f"  {label:16s}" + "".join(f"{d[s].get('mean_new_tokens',0):10,.0f}" if s in d else f"{'-':>10s}" for s in suites))
PY
echo "[stoptrain] DONE"
