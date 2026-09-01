#!/bin/bash
# md_9b_plus keeps every Blender sample the 93.9% model had (21,012) and adds 104k more CadQuery/GLSL.
# The prediction it tests: bench returns to ~93%, because what decides a dialect's score is that dialect's
# absolute sample count -- not the total, which full9b raised to 271k while cutting Blender to 6,590 and scoring 58.5%.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
RUN=runs/lf_qwen35_9b_plus
[ -f $RUN/train_results.json ] || { echo "[plus] training did not finish"; exit 1; }
if [ ! -d $RUN/merged ]; then
  sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/ict/hx_624/models/Qwen3.5-9B|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_9b_plus.yaml
  scripts/lf_export.sh configs/lf/export_9b_plus.yaml > logs/export_9b_plus.log 2>&1 || { echo "[plus] EXPORT FAILED: $(grep -iE 'error' logs/export_9b_plus.log | tail -1 | cut -c1-140)"; exit 1; }
fi
echo "[plus] $(date) exported"
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[plus] no GPU with 40 GB free"; exit 1; }
# both decodings, per the protocol the greedy-runaway finding forced
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_plus_greedy 2>&1 | tail -10
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_plus_t07 --temp 0.7 --seed 1 2>&1 | tail -10
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
python - <<'PY'
import json, os
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
sets = [("mmmix2 (192k, Blender 21k)", "q9b_mmmix2_all"), ("full9b (271k, Blender 6.6k)", "q9b_full_greedy"),
        ("md_9b_plus (296k, Blender 21k)", "q9b_plus_greedy"), ("md_9b_plus T=0.7", "q9b_plus_t07")]
print(f"  {'model':32s}" + "".join(f"{s[:8]:>10s}" for s in suites))
for label, n in sets:
    p = f"eval/out/{n}_report.json"
    if not os.path.exists(p): print(f"  {label:32s}  (missing)"); continue
    d = {x["suite"]: x for x in json.load(open(p))["suites"]}
    print(f"  {label:32s}" + "".join(f"{d[s]['exec_rate']*100:9.1f}%" if s in d else f"{'-':>10s}" for s in suites))
PY
echo "[plus] DONE"
