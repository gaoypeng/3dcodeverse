"""Held-out Blender test (bioinspired3d-style): execute generated & reference scripts in Blender, compare meshes."""
import argparse, json, os, re, subprocess, sys, numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import load_points, chamfer_f, rot_y
BLENDER = "/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender"; RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_runner.py")
ap = argparse.ArgumentParser(); ap.add_argument("--test", required=True); ap.add_argument("--gen_dir", required=True); ap.add_argument("--workers", type=int, default=32); a = ap.parse_args()
tests = [json.loads(l) for l in open(a.test)]; ref_cache = os.path.join(os.path.dirname(a.test), "ref_exec_blender")
def code_of(msg):
    m = re.findall(r"```[a-zA-Z]*\n(.*?)```", msg, flags=re.S); return (max(m, key=len) if m else msg).strip()
def run_blender(code, d):
    d = os.path.abspath(d); os.makedirs(d, exist_ok=True); sp = os.path.join(d, "code.py"); glb = os.path.join(d, "out.glb"); rp = os.path.join(d, "exec.json")
    if not os.path.exists(sp): open(sp, "w").write(code)
    try:
        subprocess.run([BLENDER, "-b", "--factory-startup", "-noaudio", "--python", RUNNER, "--", "--script", sp, "--out", glb, "--report", rp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, env={**os.environ, "HOME": "/tmp"})
        r = json.load(open(rp)) if os.path.exists(rp) else {"status": "CRASH"}
    except subprocess.TimeoutExpired: r = {"status": "TIMEOUT"}
    r["mesh"] = glb if r.get("status") == "OK" else None; return r
def ref_job(t):
    tid = t["id"].replace("/", "__"); d = os.path.join(ref_cache, tid)
    if os.path.exists(os.path.join(d, "rep.json")): return tid, json.load(open(os.path.join(d, "rep.json")))
    rep = run_blender(code_of(t["messages"][-1]["content"]), d); json.dump(rep, open(os.path.join(d, "rep.json"), "w")); return tid, rep
def gen_job(t):
    tid = t["id"].replace("/", "__"); d = os.path.join(a.gen_dir, tid)
    if not os.path.exists(os.path.join(d, "code.py")): return tid, {"status": "MISSING", "mesh": None}
    return tid, run_blender(None, d)
with ThreadPoolExecutor(a.workers) as ex: refs = dict(ex.map(ref_job, tests)); gens = dict(ex.map(gen_job, tests))
rows = []
for t in tests:
    tid = t["id"].replace("/", "__"); r = {"id": tid, "ref": refs[tid].get("status"), "gen": gens[tid].get("status"), "gen_error": (gens[tid].get("error") or "")[:200]}
    if r["ref"] == "OK" and r["gen"] == "OK":
        try:
            pr = load_points(refs[tid]["mesh"], 5000); pg = load_points(gens[tid]["mesh"], 5000)
            if pr is not None and pg is not None:
                best = min((chamfer_f(rot_y(pg, k), pr) for k in range(4)), key=lambda x: x[0]); r["chamfer"] = best[0]; r.update(best[1])
        except Exception as e: r["metric_error"] = str(e)[:100]
    rows.append(r)
n = len(rows); sc = [r for r in rows if "f@0.05" in r]
summary = {"dialect": "blender_heldout", "n": n, "ref_exec_ok": sum(r["ref"] == "OK" for r in rows), "gen_exec_ok": sum(r["gen"] == "OK" for r in rows), "gen_exec_rate": round(sum(r["gen"] == "OK" for r in rows) / n, 4),
           "n_scored": len(sc), "f@0.05_mean_scored": round(float(np.mean([r["f@0.05"] for r in sc])), 4) if sc else None, "f@0.05_mean_all": round(float(sum(r.get("f@0.05", 0) for r in rows) / n), 4), "chamfer_mean_scored": round(float(np.mean([r["chamfer"] for r in sc])), 4) if sc else None}
os.makedirs(a.gen_dir, exist_ok=True)
with open(os.path.join(a.gen_dir, "dialect_metrics.jsonl"), "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")
json.dump(summary, open(os.path.join(a.gen_dir, "summary.json"), "w"), indent=2); print(json.dumps(summary))
