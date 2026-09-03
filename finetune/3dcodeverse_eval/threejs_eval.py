"""Run every <gen_dir>/<task>/code.py (HTML) through the headless-browser runner (parallel) and summarise."""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from threejs_runner import run_threejs
ap = argparse.ArgumentParser(); ap.add_argument("--gen_dir", required=True); ap.add_argument("--workers", type=int, default=8); a = ap.parse_args()
tasks = sorted(d for d in os.listdir(a.gen_dir) if os.path.isfile(os.path.join(a.gen_dir, d, "code.py")))
def job(t):
    html = open(os.path.join(a.gen_dir, t, "code.py")).read()
    r = run_threejs(html, os.path.join(a.gen_dir, t, "exec")); r["task"] = t; r.pop("shot", None); return r
with ThreadPoolExecutor(a.workers) as ex: res = list(ex.map(job, tasks))
from collections import Counter
c = Counter(r["status"] for r in res)
with open(os.path.join(a.gen_dir, "threejs_results.jsonl"), "w") as f:
    for r in res: f.write(json.dumps(r) + "\n")
summ = {"dialect": "threejs", "n": len(res), "exec_ok": c.get("OK", 0), "exec_rate": round(c.get("OK", 0) / max(1, len(res)), 4), "counts": dict(c)}
json.dump(summ, open(os.path.join(a.gen_dir, "summary.json"), "w"), indent=2); print(json.dumps(summ))
print("failure modes:", Counter((r["error"] or r["status"])[:80] for r in res if r["status"] != "OK").most_common(6))
