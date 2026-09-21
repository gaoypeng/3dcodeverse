"""Build reference meshes by executing the reference programs of every suite that has them.

Writes $CV3D_EVAL_DATA/refs/<suite>/<id>/ref.{glb|stl} plus refs/<suite>/ref_exec.jsonl, then re-runs
build_prompts so the prompt rows point at the meshes.  Executing the reference through the *same* executor
as the model outputs is also the executor's self-test: the report's sanity figure for 3DCodeBench is
Chamfer ≈ 0.016 / F@0.05 ≈ 1.0 between a re-executed reference and the shipped GT.

For 3DCodeBench the canonical GT is `renders/object.glb` inside `ilabai/3dcodeverse/3dcodebench/factories_geo/
samples-000.tar`; when that tar is present (`download.py --gt-tar`) the canonical mesh is stored as
`ref_canonical.glb` and `ref.glb` is the re-executed one — `score.py --ref canonical` switches between them.
"""
from __future__ import annotations

import argparse
import json
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config, executors
from .extract import extract
from .suites import load_prompts

MESH_EXT = {"blender": "glb", "cadquery": "stl", "openscad": "stl"}


def ref_dir_name(suite: str) -> str:
    """All 3dcodebench_* suites share one GT set (refs/3dcodebench); held-out suites own theirs."""
    return "3dcodebench" if suite.startswith("3dcodebench") else suite


def _one(row: dict, suite_dir: Path, force: bool) -> dict:
    d = suite_dir / row["id"]
    ext = MESH_EXT[row["dialect"]]
    target = d / f"ref.{ext}"
    if target.exists() and not force:
        return {"id": row["id"], "status": "OK", "cached": True, "mesh": str(target)}
    code = row["reference"]["code"]
    if "```" in code:                       # held-out parquets store the reference inside a fence
        code = extract(code, row["dialect"])[0]
    rep = executors.run(row["dialect"], code, str(d))
    if rep.get("mesh"):
        Path(rep["mesh"]).replace(target)
        rep["mesh"] = str(target)
    return {"id": row["id"], **{k: rep.get(k) for k in ("status", "error", "latency_s", "mesh", "n_verts", "n_faces")}}


def build(suite: str, workers: int, force: bool, limit: int | None) -> None:
    rows = [r for r in load_prompts(suite) if r["reference"].get("code") and r["dialect"] in MESH_EXT]
    if limit:
        rows = rows[:limit]
    suite_dir = config.REFS_DIR / ref_dir_name(suite)
    suite_dir.mkdir(parents=True, exist_ok=True)
    print(f"[refs] {suite}: executing {len(rows)} reference programs with {workers} workers")
    res = []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_one, r, suite_dir, force): r["id"] for r in rows}
        for i, fu in enumerate(as_completed(futs), 1):
            res.append(fu.result())
            if i % 25 == 0:
                print(f"[refs] {suite} {i}/{len(rows)}", flush=True)
    res.sort(key=lambda r: r["id"])
    with (suite_dir / "ref_exec.jsonl").open("w") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    from collections import Counter
    c = Counter(r["status"] for r in res)
    print(f"[refs] {suite}: {dict(c)}  OK={c.get('OK', 0)}/{len(res)}")


def extract_canonical(tar_path: Path) -> None:
    """Pull renders/object.glb for every factory out of the 3dcodebench factories_geo tar."""
    import pyarrow.parquet as pq

    meta = pq.read_table(config.CV3D_HUB / "3dcodebench" / "factories_geo" / "metadata.parquet").to_pylist()
    want = {m["key"].replace("Factory_geo", "") + "_seed0": m for m in meta}   # AgaveMonocotFactory_geo -> AgaveMonocot_seed0
    tasks = {r["id"] for r in load_prompts("3dcodebench_text")}
    n = 0
    with tarfile.open(tar_path) as tf:
        for member in tf:
            if not member.name.endswith("object.glb"):
                continue
            key = member.name.split("/")[0] if "/" in member.name else ""
            task = key.replace("Factory_geo", "") + "_seed0"
            if task not in tasks:
                continue
            out = config.REFS_DIR / "3dcodebench" / task / "ref_canonical.glb"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tf.extractfile(member).read())
            n += 1
    print(f"[refs] canonical GT extracted for {n}/{len(tasks)} tasks (of {len(want)} factories in the tar)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", nargs="*", default=["3dcodebench_text", "heldout_blender", "heldout_cadquery", "heldout_openscad"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--canonical-tar", type=Path, default=None, help="3dcodebench/factories_geo/samples-000.tar")
    a = ap.parse_args()
    for s in a.suites:
        build(s, a.workers, a.force, a.limit)
    if a.canonical_tar:
        extract_canonical(a.canonical_tar)
    from .build_prompts import build_3dcodebench, build_heldout
    build_3dcodebench()
    build_heldout()


if __name__ == "__main__":
    main()
