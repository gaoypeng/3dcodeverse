"""Blender-Python executor: headless Blender 5.x runs the script, exports a GLB, writes a JSON report.

The in-Blender part is ``blender_runner.py`` (verbatim copy of finetune/eval/blender_runner.py): it deletes
every default object and purges orphan data first (so an empty script cannot pass on the default cube),
runs the script with ``runpy``, counts depsgraph-evaluated MESH objects, and exports ``export_yup=True``.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .. import config

RUNNER = Path(__file__).with_name("blender_runner.py")


def run(code: str, workdir: str, timeout: int = 300) -> dict:
    blender = config.find_blender()
    if blender is None:
        return {"status": "CRASH", "error": "blender not found (set CV3D_BLENDER)", "latency_s": 0.0, "mesh": None}
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    script, glb, rep_path = wd / "code.py", wd / "out.glb", wd / "exec.json"
    script.write_text(code if code.endswith("\n") else code + "\n")
    for stale in (glb, rep_path):
        if stale.exists():
            stale.unlink()
    cmd = [str(blender), "-b", "--factory-startup", "-noaudio", "--python", str(RUNNER), "--",
           "--script", str(script), "--out", str(glb), "--report", str(rep_path)]
    t0 = time.time()
    env = config.tool_env(HOME="/tmp", XDG_CONFIG_HOME="/tmp/.bcfg", SEED=os.environ.get("SEED", "0"))
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                           text=True, errors="replace", env=env, cwd=str(wd))
        if rep_path.exists():
            rep = json.loads(rep_path.read_text())
        else:
            rep = {"status": "CRASH", "error": p.stdout[-1500:]}
    except subprocess.TimeoutExpired:
        rep = {"status": "TIMEOUT", "error": f"timeout {timeout}s"}
    rep["latency_s"] = round(time.time() - t0, 1)
    rep["mesh"] = str(glb) if rep.get("status") == "OK" and glb.exists() else None
    if rep.get("status") == "OK" and rep["mesh"] is None:
        rep["status"] = "EMPTY"
    return rep
