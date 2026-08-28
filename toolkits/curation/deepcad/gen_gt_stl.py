"""
gen_gt_stl.py  -- run in the `gt` (pythonocc-core) env.

Generate DeepCAD's OFFICIAL ground-truth mesh for each model, straight from the
same cad_vec .h5 we converted, using cadlib.visualize.vec2CADsolid (OCC.Core).
This is the authoritative reference geometry to compare our CadQuery output
against (IoU). Batched: one worker imports OCC once and processes a chunk.

Usage:
  python gen_gt_stl.py --vec_dir /data/.../cad_vec --ids ids.txt \
      --out_dir /data/.../gt_stl --workers 40 --chunk 32 --deepcad /data/.../DeepCAD
"""
import os
import sys
import json
import argparse
import tempfile
import subprocess
from pathlib import Path
from joblib import Parallel, delayed
from tqdm import tqdm

WORKER_SRC = r'''
import sys, os, json, h5py, numpy as np
sys.path.insert(0, os.environ["DEEPCAD_ROOT"])
# numpy compat shim: DeepCAD's cadlib references np.int in numericalize (unused
# on this path), but guard anyway for newer numpy.
if not hasattr(np, "int"):
    np.int = int
from cadlib.visualize import vec2CADsolid
from OCC.Extend.DataExchange import write_stl_file

for line in open(sys.argv[1]):
    line = line.rstrip("\n")
    if not line:
        continue
    h5path, out = line.split("\t")
    sub = os.path.basename(os.path.dirname(h5path))
    name = os.path.splitext(os.path.basename(h5path))[0]
    mid = f"{sub}/{name}"
    try:
        with h5py.File(h5path, "r") as f:
            vec = f[list(f.keys())[0]][()]
        shape = vec2CADsolid(vec)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        write_stl_file(shape, out, linear_deflection=0.01, angular_deflection=0.3)
        ok = os.path.exists(out) and os.path.getsize(out) > 0
        print(json.dumps({"id": mid, "status": "OK" if ok else "EMPTY"}), flush=True)
    except Exception as e:
        print(json.dumps({"id": mid, "status": "FAIL", "detail": str(e)[:150]}), flush=True)
'''


def run_chunk(ids, vec_dir, out_dir, py_exe, worker_path):
    lines = []
    for mid in ids:
        sub, name = mid.split("/")
        h5 = str(Path(vec_dir) / sub / (name + ".h5"))
        out = str(Path(out_dir) / sub / (name + ".stl"))
        lines.append(f"{h5}\t{out}")
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as mf:
        mf.write("\n".join(lines))
        manifest = mf.name
    res = []
    try:
        pr = subprocess.run([py_exe, worker_path, manifest], capture_output=True,
                            text=True, timeout=40 * len(ids) + 60)
        for line in pr.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    res.append((r["id"], r["status"]))
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        os.unlink(manifest)
    done = {r[0] for r in res}
    for mid in ids:
        if mid not in done:
            res.append((mid, "CRASH"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vec_dir", required=True)
    ap.add_argument("--ids", required=True, help="text file, one model_id per line")
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--deepcad", required=True, help="DeepCAD repo root")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--chunk", type=int, default=32)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="gt_gen_report.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.environ["DEEPCAD_ROOT"] = args.deepcad
    ids = [l.strip() for l in open(args.ids) if l.strip()]
    if args.limit:
        ids = ids[: args.limit]
    worker_path = str(Path(tempfile.gettempdir()) / "_gtgen_worker.py")
    Path(worker_path).write_text(WORKER_SRC)

    chunks = [ids[i:i + args.chunk] for i in range(0, len(ids), args.chunk)]
    print(f"Generating GT STL for {len(ids)} models in {len(chunks)} chunks...")
    results = Parallel(n_jobs=args.workers)(
        delayed(run_chunk)(c, args.vec_dir, args.out_dir, args.python, worker_path)
        for c in tqdm(chunks)
    )
    flat = [r for chunk in results for r in chunk]
    buckets = {}
    for _, st in flat:
        buckets[st] = buckets.get(st, 0) + 1
    print("GT generation:", buckets)
    json.dump({"buckets": buckets,
               "fail": [r[0] for r in flat if r[1] != "OK"]},
              open(args.out, "w"), indent=2)


if __name__ == "__main__":
    main()
