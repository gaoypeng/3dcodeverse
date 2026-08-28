"""
dedup_check.py
Compute a geometry fingerprint (volume, surface area, inertia-trace) for each
STEP and flag near-duplicates. Batched like validate_fast (import cadquery once
per worker). Writes duplicates.json: {kept_id: [dup_id, ...]}.

Fingerprint is rounded so numerically-identical solids collide; this catches
exact/near duplicates within the batch (and can be diffed against an existing
corpus's fingerprints later).
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

WORKER_SRC = '''
import sys, json, os
import cadquery as cq
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
for line in open(sys.argv[1]):
    step = line.strip()
    if not step: continue
    sub = os.path.basename(os.path.dirname(step))
    name = os.path.splitext(os.path.basename(step))[0]
    try:
        shape = cq.importers.importStep(step).val()
        vp = GProp_GProps(); BRepGProp.VolumeProperties_s(shape.wrapped, vp)
        sp = GProp_GProps(); BRepGProp.SurfaceProperties_s(shape.wrapped, sp)
        mat = vp.MatrixOfInertia()
        itr = mat.Value(1,1)+mat.Value(2,2)+mat.Value(3,3)
        fp = [round(abs(vp.Mass()),4), round(sp.Mass(),4), round(itr,4)]
        print(json.dumps({"id": f"{sub}/{name}", "fp": fp}), flush=True)
    except Exception as e:
        print(json.dumps({"id": f"{sub}/{name}", "fp": None}), flush=True)
'''


def run_chunk(steps, py_exe, worker_path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as mf:
        mf.write("\n".join(str(s) for s in steps))
        manifest = mf.name
    out = []
    try:
        pr = subprocess.run([py_exe, worker_path, manifest],
                            capture_output=True, text=True,
                            timeout=30 * len(steps) + 60)
        for line in pr.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    out.append((r["id"], tuple(r["fp"]) if r["fp"] else None))
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        os.unlink(manifest)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step_dir", required=True)
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--chunk", type=int, default=64)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="duplicates.json")
    args = ap.parse_args()

    worker_path = str(Path(tempfile.gettempdir()) / "_dedup_worker.py")
    Path(worker_path).write_text(WORKER_SRC)

    steps = sorted(Path(args.step_dir).rglob("*.step"))
    chunks = [steps[i:i + args.chunk] for i in range(0, len(steps), args.chunk)]
    print(f"Fingerprinting {len(steps)} STEP files in {len(chunks)} chunks...")

    chunk_results = Parallel(n_jobs=args.workers)(
        delayed(run_chunk)(c, args.python, worker_path) for c in tqdm(chunks)
    )

    seen = {}
    dups = defaultdict(list)
    n_bad = 0
    for chunk in chunk_results:
        for mid, fp in chunk:
            if fp is None:
                n_bad += 1
                continue
            if fp in seen:
                dups[seen[fp]].append(mid)
            else:
                seen[fp] = mid

    total_dups = sum(len(v) for v in dups.values())
    print(f"Unique fingerprints: {len(seen)}, duplicate models: {total_dups}, "
          f"unreadable: {n_bad}")
    json.dump(dups, open(args.out, "w"), indent=2)


if __name__ == "__main__":
    main()
