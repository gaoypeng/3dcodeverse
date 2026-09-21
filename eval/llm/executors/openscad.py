"""OpenSCAD executor: ``openscad -o out.stl --export-format binstl model.scad`` (needs a 2025+ build)."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .. import config


def run(code: str, workdir: str, timeout: int = 120) -> dict:
    exe = config.find_openscad()
    if exe is None:
        return {"status": "CRASH", "error": "openscad not found (set CV3D_OPENSCAD)", "latency_s": 0.0, "mesh": None}
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    scad, stl = wd / "code.scad", wd / "out.stl"
    scad.write_text(code)
    if stl.exists():
        stl.unlink()
    t0 = time.time()
    try:
        p = subprocess.run([str(exe), "-o", str(stl), "--export-format", "binstl", str(scad)], cwd=str(wd),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace",
                           env=config.tool_env(HOME=str(wd), QT_QPA_PLATFORM="offscreen"))
        ok = p.returncode == 0 and stl.exists() and stl.stat().st_size > 100
        rep = {"status": "OK" if ok else ("EMPTY" if p.returncode == 0 else "FAIL"),
               "error": None if ok else p.stdout[-800:]}
    except subprocess.TimeoutExpired:
        rep = {"status": "TIMEOUT", "error": f"timeout {timeout}s"}
    rep["latency_s"] = round(time.time() - t0, 1)
    rep["mesh"] = str(stl) if rep["status"] == "OK" else None
    return rep
