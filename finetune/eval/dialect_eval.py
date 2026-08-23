"""Evaluate a model on a non-Blender dialect test set: generate (vLLM, done separately) -> execute generated & reference -> geometry metrics vs reference mesh.
usage: dialect_eval.py --dialect cadquery --test data/multidialect/cadquery/test.jsonl --gen_dir <dir with <id>/code.py> [--workers 32]"""
import argparse, json, os, re, sys, numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dialect_runners import RUNNERS
from metrics import load_points, chamfer_f, rot_y
ap = argparse.ArgumentParser(); ap.add_argument("--dialect", required=True); ap.add_argument("--test", required=True); ap.add_argument("--gen_dir", required=True); ap.add_argument("--workers", type=int, default=32); ap.add_argument("--ref_cache", default=None)
a = ap.parse_args()
run = RUNNERS[a.dialect]
tests = [json.loads(l) for l in open(a.test)]
ref_cache = a.ref_cache or os.path.join(os.path.dirname(a.test), f"ref_exec_{a.dialect}")
def code_of(msg):
    m = re.findall(r"```[a-zA-Z]*\n(.*?)```", msg, flags=re.S); return (max(m, key=len) if m else msg).strip()
def ref_job(t):
    tid = t["id"].replace("/", "__"); d = os.path.join(ref_cache, tid)
    if os.path.exists(os.path.join(d, "rep.json")): return tid, json.load(open(os.path.join(d, "rep.json")))
    rep = run(code_of(t["messages"][-1]["content"]), d); json.dump(rep, open(os.path.join(d, "rep.json"), "w")); return tid, rep
def gen_job(t):
    tid = t["id"].replace("/", "__"); d = os.path.join(a.gen_dir, tid); cp = os.path.join(d, "code.py")
    if not os.path.exists(cp): return tid, {"status": "MISSING", "error": "no generation", "mesh": None}
    rep = run(open(cp).read(), os.path.join(d, "exec")); return tid, rep
with ThreadPoolExecutor(a.workers) as ex:
    refs = dict(ex.map(ref_job, tests)); gens = dict(ex.map(gen_job, tests))
rows = []; np.random.seed(0)
for t in tests:
    tid = t["id"].replace("/", "__"); r = {"id": tid, "ref": refs[tid]["status"], "gen": gens[tid]["status"], "gen_error": (gens[tid].get("error") or "")[:200]}
    if a.dialect != "glsl" and refs[tid]["status"] == "OK" and gens[tid]["status"] == "OK":
        try:
            pr = load_points(refs[tid]["mesh"], 5000); pg = load_points(gens[tid]["mesh"], 5000)
            if pr is not None and pg is not None:
                best = min((chamfer_f(rot_y(pg, k), pr) for k in range(4)), key=lambda x: x[0]); r["chamfer"] = best[0]; r.update(best[1])
        except Exception as e: r["metric_error"] = str(e)[:100]
    rows.append(r)
n = len(rows); ref_ok = sum(r["ref"] == "OK" for r in rows); gen_ok = sum(r["gen"] == "OK" for r in rows); sc = [r for r in rows if "f@0.05" in r]
summary = {"dialect": a.dialect, "n": n, "ref_exec_ok": ref_ok, "gen_exec_ok": gen_ok, "gen_exec_rate": round(gen_ok / n, 4), "n_scored": len(sc),
           "f@0.05_mean_scored": round(float(np.mean([r["f@0.05"] for r in sc])), 4) if sc else None, "f@0.1_mean_scored": round(float(np.mean([r["f@0.1"] for r in sc])), 4) if sc else None,
           "f@0.05_mean_all": round(float(sum(r.get("f@0.05", 0) for r in rows) / n), 4), "chamfer_mean_scored": round(float(np.mean([r["chamfer"] for r in sc])), 4) if sc else None}
os.makedirs(a.gen_dir, exist_ok=True)
with open(os.path.join(a.gen_dir, "dialect_metrics.jsonl"), "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")
json.dump(summary, open(os.path.join(a.gen_dir, "summary.json"), "w"), indent=2); print(json.dumps(summary))
from collections import Counter; print("gen failure modes:", Counter(re.sub(r"'[^']*'", "'<x>'", (r["gen_error"] or r["gen"]).split("\n")[0][:70]) for r in rows if r["gen"] != "OK").most_common(8))
