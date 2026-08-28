"""
dedup_fast.py
Geometry-fingerprint dedup using the already-generated ground-truth STLs
(trimesh reads STL directly -- far faster than re-importing STEP).

Fingerprint = (volume, area, inertia-trace), rounded. Exact-match collisions
are flagged as duplicates. Writes duplicates.json: {kept_id: [dup_id,...]}.
"""
import os
import sys
import json
import argparse
import tempfile
import subprocess
from pathlib import Path
from collections import defaultdict
from joblib import Parallel, delayed
from tqdm import tqdm

WORKER_SRC = r'''
import sys, os, json
import numpy as np
import trimesh
for line in open(sys.argv[1]):
    stl = line.strip()
    if not stl: continue
    sub=os.path.basename(os.path.dirname(stl)); name=os.path.splitext(os.path.basename(stl))[0]
    mid=f"{sub}/{name}"
    try:
        m = trimesh.load(stl, force="mesh")
        vol=round(abs(float(m.volume)),4); area=round(float(m.area),4)
        itr=round(float(np.trace(m.moment_inertia)),4)
        print(json.dumps({"id":mid,"fp":[vol,area,itr]}), flush=True)
    except Exception as e:
        print(json.dumps({"id":mid,"fp":None}), flush=True)
'''


def run_chunk(ids, stl_dir, py_exe, worker_path):
    lines = [str(Path(stl_dir) / mid.split("/")[0] / (mid.split("/")[1] + ".stl")) for mid in ids]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as mf:
        mf.write("\n".join(lines))
        manifest = mf.name
    res = []
    try:
        pr = subprocess.run([py_exe, worker_path, manifest], capture_output=True,
                            text=True, timeout=10 * len(ids) + 60)
        for line in pr.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    res.append((r["id"], tuple(r["fp"]) if r["fp"] else None))
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        os.unlink(manifest)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stl_dir", required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--chunk", type=int, default=64)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="duplicates.json")
    args = ap.parse_args()

    ids = [l.strip() for l in open(args.ids) if l.strip()]
    worker_path = str(Path(tempfile.gettempdir()) / "_dedupfast_worker.py")
    Path(worker_path).write_text(WORKER_SRC)
    chunks = [ids[i:i + args.chunk] for i in range(0, len(ids), args.chunk)]
    print(f"Fingerprinting {len(ids)} models in {len(chunks)} chunks...")

    results = Parallel(n_jobs=args.workers)(
        delayed(run_chunk)(c, args.stl_dir, args.python, worker_path) for c in tqdm(chunks)
    )
    seen = {}
    dups = defaultdict(list)
    n_bad = 0
    for chunk in results:
        for mid, fp in chunk:
            if fp is None:
                n_bad += 1
                continue
            if fp in seen:
                dups[seen[fp]].append(mid)
            else:
                seen[fp] = mid
    total = sum(len(v) for v in dups.values())
    print(f"Unique: {len(seen)}, duplicate models: {total}, unreadable: {n_bad}")
    print(f"Dup groups (>=1 dup): {len(dups)}")
    json.dump(dict(dups), open(args.out, "w"))


if __name__ == "__main__":
    main()
