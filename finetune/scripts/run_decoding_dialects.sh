#!/bin/bash
# T=0.7 took the image-only model from 35.8% to 48.6% and cut unfinished generations from 93 to 21. Every
# fine-tuned model's 2-6% on OpenSCAD and 32-52% on three.js was measured with GREEDY. If those collapses are the
# same pathology, they are a decoding artifact and the training data was never the problem. Test it on the best
# model we have before redesigning any mix.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
M=runs/lf_qwen35_9b_mmmix2/merged
[ -d "$M" ] || { echo "[decdia] no model"; exit 0; }
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[decdia] no GPU with 40 GB free"; exit 0; }
echo "[decdia] $(date) on GPU $G"
# greedy numbers already exist as q9b_mmmix2_all; this adds the sampled counterpart, same harness, same suites
GPUS=$G TP=1 bash eval/eval_all_run2.sh $M q9b_mmmix2_t07 --temp 0.7 --seed 1 \
  --suites openscad threejs glsl cadquery bench 2>&1 | tail -12
python - <<'PY'
import json, os
suites = ["bench", "cadquery", "openscad", "glsl", "threejs"]
g = f"eval/out/q9b_mmmix2_all_report.json"; t = f"eval/out/q9b_mmmix2_t07_report.json"
if not (os.path.exists(g) and os.path.exists(t)):
    print("[decdia] missing a report"); raise SystemExit
G = {s["suite"]: s for s in json.load(open(g))["suites"]}
T = {s["suite"]: s for s in json.load(open(t))["suites"]}
print(f"  {'suite':10s} {'greedy':>8s} {'T=0.7':>8s} {'delta':>8s}   {'greedy tok':>11s} {'T=0.7 tok':>10s}")
for s in suites:
    if s not in G or s not in T: continue
    a, b = G[s]["exec_rate"] * 100, T[s]["exec_rate"] * 100
    print(f"  {s:10s} {a:7.1f}% {b:7.1f}% {b-a:+7.1f}pp   {G[s].get('mean_new_tokens',0):11.0f} {T[s].get('mean_new_tokens',0):10.0f}")
PY
echo "[decdia] DONE"
