"""
dedup_vec.py
Correct dedup: two models are TRUE duplicates iff their source DeepCAD vec is
identical (deterministic conversion => identical code & geometry). Geometry-only
fingerprints over-merge distinct designs that happen to share volume/area, so we
key on the exact quantized command sequence instead.

Writes duplicates_vec.json: {kept_id: [dup_id, ...]}.
"""
import os
import sys
import json
import hashlib
import argparse
import tempfile
import subprocess
from pathlib import Path
from collections import defaultdict
from joblib import Parallel, delayed
from tqdm import tqdm

WORKER_SRC = r'''
import sys, os, json, hashlib, h5py
for line in open(sys.argv[1]):
    h5 = line.strip()
    if not h5: continue
    sub=os.path.basename(os.path.dirname(h5)); name=os.path.splitext(os.path.basename(h5))[0]
    mid=f"{sub}/{name}"
    try:
        with h5py.File(h5,"r") as f:
            arr = f[list(f.keys())[0]][()]
        print(json.dumps({"id":mid,"h":hashlib.md5(arr.tobytes()).hexdigest()}), flush=True)
    except Exception:
        print(json.dumps({"id":mid,"h":None}), flush=True)
'''


def run_chunk(ids, vec_dir, py_exe, worker_path):
    lines = [str(Path(vec_dir) / mid.split("/")[0] / (mid.split("/")[1] + ".h5")) for mid in ids]
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
                    res.append((r["id"], r["h"]))
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        os.unlink(manifest)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vec_dir", required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--chunk", type=int, default=128)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="duplicates_vec.json")
    args = ap.parse_args()

    ids = [l.strip() for l in open(args.ids) if l.strip()]
    worker_path = str(Path(tempfile.gettempdir()) / "_dedupvec_worker.py")
    Path(worker_path).write_text(WORKER_SRC)
    chunks = [ids[i:i + args.chunk] for i in range(0, len(ids), args.chunk)]
    print(f"Hashing {len(ids)} vec sequences in {len(chunks)} chunks...")

    results = Parallel(n_jobs=args.workers)(
        delayed(run_chunk)(c, args.vec_dir, args.python, worker_path) for c in tqdm(chunks)
    )
    seen = {}
    dups = defaultdict(list)
    n_bad = 0
    for chunk in results:
        for mid, h in chunk:
            if h is None:
                n_bad += 1
                continue
            if h in seen:
                dups[seen[h]].append(mid)
            else:
                seen[h] = mid
    total = sum(len(v) for v in dups.values())
    print(f"Unique vec sequences: {len(seen)}, TRUE duplicate models: {total}, "
          f"unreadable: {n_bad}")
    print(f"Duplicate groups: {len(dups)}")
    json.dump(dict(dups), open(args.out, "w"))


if __name__ == "__main__":
    main()
