"""
validate_fast.py -- fast batched validation driver.

Splits the .py scripts into chunks; each chunk runs in ONE subprocess
(cq_check_batch.py) that imports cadquery once and emits a JSON line per model.
If a chunk's subprocess crashes (OCC segfault) and emits only a partial tail,
the unprocessed models are retried in a tiny isolated chunk so one bad model
can't drop its neighbours.

~50x fewer interpreter starts than the per-model subprocess approach.
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

WORKER = str(Path(__file__).with_name("cq_check_batch.py"))


def run_chunk(py_paths, step_root, py_exe, per_model_timeout):
    """Run one chunk; return list of (id, status, detail). Handles partial crash."""
    manifest_lines = []
    id_order = []
    for p in py_paths:
        sub = p.parent.name
        name = p.stem
        step_out = str(Path(step_root) / sub / (name + ".step"))
        manifest_lines.append(f"{p}\t{step_out}")
        id_order.append(f"{sub}/{name}")

    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as mf:
        mf.write("\n".join(manifest_lines))
        manifest = mf.name

    results = {}
    try:
        proc = subprocess.run([py_exe, WORKER, manifest],
                              capture_output=True, text=True,
                              timeout=per_model_timeout * len(py_paths) + 60)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                results[r["id"]] = (r["status"], r.get("detail", ""))
            except Exception:
                pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        os.unlink(manifest)

    # Models with no output -> the worker crashed/timed out before reaching them.
    missing = [pid for pid in id_order if pid not in results]
    out = [(pid, results[pid][0], results[pid][1]) for pid in id_order if pid in results]

    # Retry missing ones individually (full isolation). If a single model still
    # produces nothing, it crashed the interpreter -> mark CRASH.
    for pid in missing:
        sub, name = pid.split("/")
        p = next(pp for pp in py_paths if pp.parent.name == sub and pp.stem == name)
        step_out = str(Path(step_root) / sub / (name + ".step"))
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as mf:
            mf.write(f"{p}\t{step_out}")
            m1 = mf.name
        try:
            pr = subprocess.run([py_exe, WORKER, m1], capture_output=True,
                                text=True, timeout=per_model_timeout + 60)
            line = pr.stdout.strip().split("\n")[-1] if pr.stdout.strip() else ""
            if line:
                r = json.loads(line)
                out.append((pid, r["status"], r.get("detail", "")))
            else:
                out.append((pid, "CRASH", "interpreter died"))
        except Exception:
            out.append((pid, "CRASH", "timeout/exception"))
        finally:
            os.unlink(m1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cq_dir", required=True)
    ap.add_argument("--step_dir", required=True)
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--chunk", type=int, default=64)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="validation_results.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    py_files = sorted(Path(args.cq_dir).rglob("*.py"))
    if args.limit:
        py_files = py_files[: args.limit]
    chunks = [py_files[i:i + args.chunk] for i in range(0, len(py_files), args.chunk)]
    print(f"Validating {len(py_files)} scripts in {len(chunks)} chunks "
          f"x {args.workers} workers (chunk={args.chunk})...")

    chunk_results = Parallel(n_jobs=args.workers)(
        delayed(run_chunk)(c, args.step_dir, args.python, args.timeout)
        for c in tqdm(chunks)
    )

    by_status = {}
    for chunk in chunk_results:
        for mid, status, detail in chunk:
            by_status.setdefault(status, []).append({"id": mid, "detail": detail})

    summary = {k: len(v) for k, v in by_status.items()}
    total = sum(summary.values())
    ok = summary.get("OK", 0)
    print("Summary:", summary)
    print(f"OK rate: {ok}/{total} = {100*ok/max(total,1):.1f}%")
    json.dump(by_status, open(args.out, "w"), indent=2)


if __name__ == "__main__":
    main()
