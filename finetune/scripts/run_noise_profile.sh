#!/bin/bash
# OpenSCAD moved 40.0% -> 30.0% between two runs of the SAME model through the SAME harness, ten times the
# +-2pp floor I estimated from 3DCodeBench. So the floor is not one number: it differs per suite, and long-output
# dialects swing hardest. Run one model through all six suites three times and report the per-suite spread, so
# every future comparison can be read against the right threshold instead of a guessed one.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
M=runs/lf_qwen35_9b_mm_text/merged
[ -d "$M" ] || { echo "[noise] no model"; exit 0; }
for R in 1 2 3; do
  G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
  [ -z "$G" ] && { echo "[noise] run $R skipped: no GPU with 40 GB free"; continue; }
  echo "[noise] $(date) repeat $R of 3 on GPU $G"
  GPUS=$G TP=1 bash eval/eval_all_run2.sh $M q9b_noise_r$R 2>&1 | tail -3
done
python - <<'PY'
import json, os, statistics as st
suites = ["bench", "blender", "cadquery", "openscad", "glsl", "threejs"]
runs = []
for r in (1, 2, 3):
    p = f"eval/out/q9b_noise_r{r}_report.json"
    if os.path.exists(p):
        runs.append({s["suite"]: s["exec_rate"] for s in json.load(open(p))["suites"]})
if len(runs) < 2:
    print("[noise] fewer than two runs completed — cannot estimate a spread"); raise SystemExit
print(f"[noise] {len(runs)} repeats of an IDENTICAL model+harness")
print(f"  {'suite':10s} " + " ".join(f"run{i+1:>6}" for i in range(len(runs))) + f" {'spread':>9s}")
for s in suites:
    v = [r[s] * 100 for r in runs if s in r]
    if len(v) < 2: continue
    print(f"  {s:10s} " + " ".join(f"{x:9.1f}" for x in v) + f" {max(v)-min(v):8.1f}pp")
PY
echo "[noise] DONE"
