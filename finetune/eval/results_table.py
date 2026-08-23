"""Collect all eval/out/*/summary.json + training stats into a single JSON (for the final report)."""
import glob, json, os
OUT = "/wekafs/ict/hx_624/llm-ft/eval/out"; RUNS = "/wekafs/ict/hx_624/llm-ft/runs"
rows = []
for d in sorted(glob.glob(f"{OUT}/*/")):
    name = os.path.basename(d.rstrip("/"))
    if name.startswith("_"): continue
    sp = os.path.join(d, "summary.json")
    if not os.path.exists(sp): continue
    s = json.load(open(sp))
    gens = [json.loads(l) for f in glob.glob(os.path.join(d, "gens_shard*.jsonl")) for l in open(f)]
    avg_tok = sum(g["n_new_tokens"] for g in gens) / max(len(gens), 1)
    run = name.replace("qwen35_9b_lora_", "lf_qwen35_9b_lora_").replace("qwen35_9b_full_v1", "lf_qwen35_9b_full_v1")
    tr = {}
    trp = os.path.join(RUNS, run.split("_ckpt")[0], "train_results.json")
    if os.path.exists(trp): tr = json.load(open(trp))
    rows.append(dict(name=name, exec_ok=s["exec_ok"], exec_rate=s["exec_ok_rate"], cd=s["chamfer_mean_scored"], f05=s["f@0.05_mean_scored"], f10=s["f@0.1_mean_scored"],
                     f05_all=s["f@0.05_mean_all(fail=0)"], f10_all=s["f@0.1_mean_all(fail=0)"], avg_tok=round(avg_tok), train_loss=tr.get("train_loss"), train_min=(tr.get("train_runtime") or 0) / 60))
json.dump(rows, open("/wekafs/ict/hx_624/llm-ft/eval/all_results.json", "w"), indent=1)
for r in rows: print(f"{r['name']:36s} exec={r['exec_ok']:3d} ({r['exec_rate']*100:4.1f}%)  CD={r['cd'] if r['cd'] is None else round(r['cd'],3)}  F05={r['f05'] if r['f05'] is None else round(r['f05'],3)}  F05all={round(r['f05_all'],3)}  tok={r['avg_tok']}  loss={r['train_loss'] if r['train_loss'] is None else round(r['train_loss'],3)}  {r['train_min']:.0f}min")
