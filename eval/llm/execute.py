"""Execute every generated program in a gen dir with the suite's executor → <gen_dir>/exec_results.jsonl."""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import executors
from .executors import CODE_EXT

# Blender and Chromium are heavy; CadQuery/OpenSCAD/glslang are light.
DEFAULT_WORKERS = {"blender": 8, "cadquery": 8, "openscad": 6, "glsl": 6, "threejs": 4}


def _one(dialect: str, task_dir: Path, timeout: int | None) -> dict:
    code_path = task_dir / f"code.{CODE_EXT[dialect]}"
    code = code_path.read_text() if code_path.exists() else ""
    if not code.strip():
        rep = {"status": "FAIL", "error": "empty answer (no code extracted)", "latency_s": 0.0, "mesh": None}
    else:
        rep = executors.run(dialect, code, str(task_dir / "exec"), timeout)
    rep = {k: v for k, v in rep.items() if k not in ("traceback", "stdout_tail")} | {"id": task_dir.name}
    return rep


def execute_dir(gen_dir: Path, dialect: str, workers: int | None = None, timeout: int | None = None,
                ids: list[str] | None = None, resume: bool = True) -> list[dict]:
    gen_dir = Path(gen_dir)
    out_path = gen_dir / "exec_results.jsonl"
    prev: dict[str, dict] = {}
    if resume and out_path.exists():
        for l in out_path.open():
            r = json.loads(l)
            prev[r["id"]] = r
    task_dirs = sorted(p for p in gen_dir.iterdir() if p.is_dir() and (ids is None or p.name in ids))
    todo = [p for p in task_dirs if p.name not in prev]
    workers = workers or DEFAULT_WORKERS[dialect]
    print(f"[exec] {gen_dir.name} ({dialect}): {len(todo)} to run, {len(prev)} cached, {workers} workers", flush=True)
    res = list(prev.values())
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_one, dialect, p, timeout): p for p in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            res.append(fu.result())
            if i % 25 == 0:
                print(f"[exec] {gen_dir.name} {i}/{len(todo)}", flush=True)
    res.sort(key=lambda r: r["id"])
    with out_path.open("w") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    c = Counter(r["status"] for r in res)
    print(f"[exec] {gen_dir.name}: {dict(c)} | OK {c.get('OK', 0)}/{len(res)} = {100 * c.get('OK', 0) / max(1, len(res)):.1f}%", flush=True)
    return res


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("gen_dir", type=Path)
    ap.add_argument("--dialect", required=True, choices=sorted(CODE_EXT))
    ap.add_argument("--workers", type=int)
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()
    execute_dir(a.gen_dir, a.dialect, a.workers, a.timeout, resume=not a.no_resume)
