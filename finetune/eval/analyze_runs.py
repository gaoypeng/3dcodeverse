"""Cross-run analysis: per-task success overlap, code length vs success, per-category success. Usage: analyze_runs.py run1 run2 ..."""
import json, os, sys, collections, numpy as np
OUT = "/wekafs/ict/hx_624/llm-ft/eval/out"
runs = sys.argv[1:]
data = {}
for r in runs:
    ex = {json.loads(l)["task"]: json.loads(l) for l in open(f"{OUT}/{r}/exec_results.jsonl")}
    gens = {json.loads(l)["task"]: json.loads(l) for f in os.listdir(f"{OUT}/{r}") if f.startswith("gens_shard") for l in open(f"{OUT}/{r}/{f}")}
    met = {json.loads(l)["task"]: json.loads(l) for l in open(f"{OUT}/{r}/metrics.jsonl")} if os.path.exists(f"{OUT}/{r}/metrics.jsonl") else {}
    data[r] = (ex, gens, met)
tasks = sorted(next(iter(data.values()))[0].keys())
print("=== success by output length bucket (tokens) ===")
for r in runs:
    ex, gens, met = data[r]; b = collections.defaultdict(lambda: [0, 0])
    for t in tasks:
        n = gens.get(t, {}).get("n_new_tokens", 0); k = "<500" if n < 500 else "<1000" if n < 1000 else "<2000" if n < 2000 else "<4000" if n < 4000 else ">=4000"
        b[k][1] += 1; b[k][0] += ex[t]["status"] == "OK"
    print(f"{r:32s}", {k: f"{v[0]}/{v[1]}" for k, v in sorted(b.items())})
print("\n=== pairwise overlap of OK tasks ===")
oks = {r: {t for t in tasks if data[r][0][t]["status"] == "OK"} for r in runs}
for i, a in enumerate(runs):
    for b_ in runs[i+1:]:
        print(f"{a} ∩ {b_}: {len(oks[a] & oks[b_])} | only {a}: {len(oks[a]-oks[b_])} | only {b_}: {len(oks[b_]-oks[a])}")
print("\n=== per-task category (first word of task name) success counts for each run ===")
cats = collections.defaultdict(list)
for t in tasks: cats[t[:1]].append(t)
print("(by first letter, just to eyeball)")
for r in runs:
    ex = data[r][0]; print(f"{r:32s}", " ".join(f"{c}:{sum(ex[t]['status']=='OK' for t in ts)}/{len(ts)}" for c, ts in sorted(cats.items())))
print("\n=== best/worst tasks (F@0.05) in", runs[-1], "===")
met = data[runs[-1]][2]; sc = sorted([(m.get("f@0.05", -1), t) for t, m in met.items() if "f@0.05" in m], reverse=True)
print("best:", sc[:8]); print("worst:", sc[-8:])
