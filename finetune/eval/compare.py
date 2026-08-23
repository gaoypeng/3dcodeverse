"""Tabulate eval/out/*/summary.json (+ generation stats) into one table."""
import glob, json, os, sys
rows = []
for d in sorted(glob.glob("/wekafs/ict/hx_624/llm-ft/eval/out/*/")):
    sp = os.path.join(d, "summary.json")
    if not os.path.exists(sp): continue
    s = json.load(open(sp))
    gens = [json.loads(l) for f in glob.glob(os.path.join(d, "gens_shard*.jsonl")) for l in open(f)]
    ntok = sum(g["n_new_tokens"] for g in gens) / max(len(gens), 1)
    fin = sum(g["finished"] for g in gens) / max(len(gens), 1)
    rows.append((os.path.basename(d.rstrip("/")), s["exec_ok"], s["n_tasks"], s["exec_ok_rate"], s["chamfer_mean_scored"], s["chamfer_median_scored"], s["f@0.05_mean_scored"], s["f@0.1_mean_scored"], s["f@0.05_mean_all(fail=0)"], s["f@0.1_mean_all(fail=0)"], ntok, fin))
hdr = ("run", "exec_ok", "n", "ok_rate", "CD_mean", "CD_med", "F@.05(ok)", "F@.1(ok)", "F@.05(all)", "F@.1(all)", "avg_tok", "finished")
print("| " + " | ".join(hdr) + " |"); print("|" + "---|" * len(hdr))
for r in rows:
    print("| " + " | ".join(f"{x:.3f}" if isinstance(x, float) else str(x) for x in r) + " |")
