"""
validate_all.py
Run each generated CadQuery .py through cq_check.py (subprocess: OCC volume +
validity + STEP export). Writes validation_results.json.

Statuses: OK / INVALID / DEGENERATE / EXEC_FAIL / GEOM_FAIL / EXPORT_FAIL / TIMEOUT / NO_OUTPUT
"""
import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from joblib import Parallel, delayed
from tqdm import tqdm

CHECKER = str(Path(__file__).with_name("cq_check.py"))


def validate_one(py_path, step_root, py_exe, timeout):
    sub = py_path.parent.name
    name = py_path.stem
    model_id = f"{sub}/{name}"
    step_path = Path(step_root) / sub / (name + ".step")

    try:
        r = subprocess.run([py_exe, CHECKER, str(py_path), str(step_path)],
                           timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return model_id, "TIMEOUT", ""

    out = r.stdout.strip().split("\n")[-1] if r.stdout.strip() else ""
    if not out:
        return model_id, "EXEC_FAIL", (r.stderr.strip().split("\n")[-1][:200] if r.stderr else "no stdout")
    try:
        res = json.loads(out)
    except Exception:
        return model_id, "NO_OUTPUT", out[:200]
    return model_id, res.get("status", "NO_OUTPUT"), res.get("detail", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cq_dir", required=True)
    ap.add_argument("--step_dir", required=True)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="validation_results.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    py_files = sorted(Path(args.cq_dir).rglob("*.py"))
    if args.limit:
        py_files = py_files[: args.limit]
    print(f"Validating {len(py_files)} scripts with {args.workers} workers...")

    results = Parallel(n_jobs=args.workers)(
        delayed(validate_one)(p, args.step_dir, args.python, args.timeout)
        for p in tqdm(py_files)
    )

    by_status = {}
    for mid, status, detail in results:
        by_status.setdefault(status, []).append({"id": mid, "detail": detail})

    summary = {k: len(v) for k, v in by_status.items()}
    total = sum(summary.values())
    ok = summary.get("OK", 0)
    print("Summary:", summary)
    print(f"OK rate: {ok}/{total} = {100*ok/max(total,1):.1f}%")
    json.dump(by_status, open(args.out, "w"), indent=2)


if __name__ == "__main__":
    main()
