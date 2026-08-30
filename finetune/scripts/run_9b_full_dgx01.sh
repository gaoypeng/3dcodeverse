#!/bin/bash
# The proper 9B run, on the seven empty cards of dgx01 (dgx03 has every card shared and has been the source of
# most of the OOMs). Recipe = mmmix2's, which is the best SFT so far, with 34% more distinct code: every dialect,
# one caption per sample (caption augmentation is already known not to add anything), plus the image sets.
# Scored under BOTH decodings, because greedy alone is what hid the runaway problem for the whole project.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_full
GPUS_USE=${GPUS:-0,1,2,3,4,5,7}
echo "[full9b] $(date) train on dgx01 GPUs $GPUS_USE"
GPUS=$GPUS_USE scripts/lf_train.sh configs/lf/lora_9b_full.yaml > logs/train_9b_full.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[full9b] TRAIN FAILED: $(grep -iE 'out of memory|Error|Traceback' logs/train_9b_full.log | grep -v errors | tail -1 | cut -c1-190)"; exit 1; }
echo "[full9b] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-150)"
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_full.yaml
scripts/lf_export.sh configs/lf/export_9b_full.yaml > logs/export_9b_full.log 2>&1 || { echo "[full9b] EXPORT FAILED"; exit 1; }
echo "[full9b] $(date) exported"
G1=$(echo $GPUS_USE | cut -d, -f1)
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_full_greedy 2>&1 | tail -10
GPUS=$G1 TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_full_t07 --temp 0.7 --seed 1 2>&1 | tail -10
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
python - <<'PY'
import json, os
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
sets = [("mmmix2 greedy", "q9b_mmmix2_all"), ("mmmix2 T=0.7", "q9b_mmmix2_t07"),
        ("full9b greedy", "q9b_full_greedy"), ("full9b T=0.7", "q9b_full_t07")]
for title, key in (("exec rate", "exec_rate"), ("mean output tokens", "mean_new_tokens")):
    print(f"  --- {title} ---")
    print(f"  {'model':16s}" + "".join(f"{s[:8]:>10s}" for s in suites))
    for label, n in sets:
        p = f"eval/out/{n}_report.json"
        if not os.path.exists(p): print(f"  {label:16s}  (missing)"); continue
        d = {x["suite"]: x for x in json.load(open(p))["suites"]}
        if key == "exec_rate":
            print(f"  {label:16s}" + "".join(f"{d[s][key]*100:9.1f}%" if s in d else f"{'-':>10s}" for s in suites))
        else:
            print(f"  {label:16s}" + "".join(f"{d[s].get(key,0):10,.0f}" if s in d else f"{'-':>10s}" for s in suites))
PY
echo "[full9b] ALL DONE"
