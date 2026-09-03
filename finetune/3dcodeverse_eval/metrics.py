"""Geometry metrics between generated GLB and the 3DCodeBench ground-truth GLB.
Normalisation: center at bbox center, scale so max bbox extent = 1. Metrics: symmetric Chamfer-L2 distance (mean NN distance),
F-score @0.05 / @0.1, best over 4 yaw rotations (objects may face any direction). Writes <gen_dir>/metrics.jsonl + summary.json"""
import argparse, json, os, numpy as np
import trimesh
from scipy.spatial import cKDTree

BENCH = os.environ.get("BENCH_DATA", "/wekafs/ict/hx_624/data/3dcodebench/data")

def load_points(path, n):
    m = trimesh.load(path, force="mesh")
    if isinstance(m, trimesh.Scene): m = trimesh.util.concatenate(list(m.geometry.values()))
    if m.is_empty or len(m.faces) == 0: return None
    v = m.vertices
    c = (v.max(0) + v.min(0)) / 2; s = (v.max(0) - v.min(0)).max()
    if s <= 0: return None
    m.apply_translation(-c); m.apply_scale(1.0 / s)
    pts, _ = trimesh.sample.sample_surface(m, n)
    return np.asarray(pts)

def chamfer_f(a, b, taus=(0.05, 0.1)):
    ta, tb = cKDTree(a), cKDTree(b)
    da, _ = tb.query(a); db, _ = ta.query(b)
    cd = float(da.mean() + db.mean())
    fs = {}
    for t in taus:
        p = float((da < t).mean()); r = float((db < t).mean())
        fs[f"f@{t}"] = 2 * p * r / (p + r) if p + r > 0 else 0.0
    return cd, fs

def rot_y(p, k):
    th = np.pi / 2 * k; c, s = np.cos(th), np.sin(th)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return p @ R.T

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen_dir", required=True)
    ap.add_argument("--n_points", type=int, default=10000)
    a = ap.parse_args()
    np.random.seed(0)
    ex = {json.loads(l)["task"]: json.loads(l) for l in open(os.path.join(a.gen_dir, "exec_results.jsonl"))}
    tasks = sorted(os.listdir(BENCH))
    rows = []
    for t in tasks:
        r = {"task": t, "exec": ex.get(t, {}).get("status", "MISSING")}
        gen_glb = os.path.join(a.gen_dir, t, "out.glb"); gt_glb = os.path.join(BENCH, t, "glb", f"{t}.glb")
        if r["exec"] == "OK" and os.path.exists(gen_glb) and os.path.exists(gt_glb):
            try:
                pg = load_points(gen_glb, a.n_points); pt = load_points(gt_glb, a.n_points)
                if pg is not None and pt is not None:
                    best = None
                    for k in range(4):
                        cd, fs = chamfer_f(rot_y(pg, k), pt)
                        if best is None or cd < best[0]: best = (cd, fs)
                    r.update(chamfer=best[0], **best[1])
            except Exception as e:
                r["metric_error"] = str(e)[:200]
        rows.append(r)
    with open(os.path.join(a.gen_dir, "metrics.jsonl"), "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    n = len(rows); ok = [r for r in rows if r["exec"] == "OK"]; sc = [r for r in rows if "chamfer" in r]
    # Failed / empty generations get the worst score for the aggregate (chamfer: mean of worst-10 for penalty; f: 0)
    summary = {
        "n_tasks": n, "exec_ok": len(ok), "exec_ok_rate": round(len(ok) / n, 4), "n_scored": len(sc),
        "chamfer_mean_scored": round(float(np.mean([r["chamfer"] for r in sc])), 4) if sc else None,
        "chamfer_median_scored": round(float(np.median([r["chamfer"] for r in sc])), 4) if sc else None,
        "f@0.05_mean_scored": round(float(np.mean([r["f@0.05"] for r in sc])), 4) if sc else None,
        "f@0.1_mean_scored": round(float(np.mean([r["f@0.1"] for r in sc])), 4) if sc else None,
        "f@0.05_mean_all(fail=0)": round(float(sum(r.get("f@0.05", 0) for r in rows) / n), 4),
        "f@0.1_mean_all(fail=0)": round(float(sum(r.get("f@0.1", 0) for r in rows) / n), 4),
    }
    json.dump(summary, open(os.path.join(a.gen_dir, "summary.json"), "w"), indent=2)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
