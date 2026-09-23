"""CadQuery executor: runs the script in a subprocess interpreter that has cadquery, exports STL.

Result discovery (ported from finetune/eval/dialect_runners.py): export calls are intercepted (scripts export
STEP/STL to arbitrary paths), ``show_object``/``debug`` are injected, and the result object is the last
captured export → a global named ``result/final/model/part/solid/shape/body/assembly/assy/obj/res`` → any
Workplane/Shape/Assembly global.  Assemblies are compounded; empty shapes are rejected.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .. import config

CQ_RUNNER = r'''
import sys, json, runpy, traceback, os, importlib
os.environ.setdefault("OMP_NUM_THREADS", "1")
script, out_stl = sys.argv[1], sys.argv[2]
rep = {"status": "FAIL", "error": None}
try:
    import cadquery as cq
    sys.argv = [script, out_stl]
    _orig_export = cq.exporters.export
    captured = []
    def _capture(o, *a, **k): captured.append(o)
    for modname in ("cadquery.occ_impl.exporters", "cadquery.exporters"):
        try:
            _m = importlib.import_module(modname); _m.export = _capture
        except Exception: pass
    try: cq.exporters.export = _capture
    except Exception: pass
    g = runpy.run_path(script, run_name="__main__", init_globals={"show_object": _capture, "debug": _capture})
    obj = None
    for c in reversed(captured):
        if isinstance(c, (cq.Workplane, cq.Shape, cq.Assembly)): obj = c; break
    if obj is None:
        for name in ("result", "final", "model", "part", "solid", "shape", "body", "assembly", "assy", "obj", "res"):
            if isinstance(g.get(name), (cq.Workplane, cq.Shape, cq.Assembly)): obj = g[name]; break
    if obj is None:
        cands = [v for v in g.values() if isinstance(v, (cq.Workplane, cq.Shape, cq.Assembly))]
        obj = cands[-1] if cands else None
    if obj is None: raise RuntimeError("no CadQuery result object found (expected variable `result`)")
    if isinstance(obj, cq.Assembly): obj = obj.toCompound()
    if isinstance(obj, cq.Workplane):
        vals = obj.vals()
        if not vals: raise RuntimeError("empty workplane")
        obj = vals[0] if len(vals) == 1 else cq.Compound.makeCompound([v for v in vals if isinstance(v, cq.Shape)])
    if not isinstance(obj, cq.Shape): raise RuntimeError(f"unsupported object {type(obj)}")
    if obj.Volume() <= 1e-9 and not obj.Faces(): raise RuntimeError("empty shape")
    _orig_export(obj, out_stl, tolerance=0.01, angularTolerance=0.1)
    rep["status"] = "OK"
except BaseException as e:
    rep["error"] = (type(e).__name__ + ": " + str(e))[:500]; rep["traceback"] = traceback.format_exc()[-1500:]
print("CQ_REPORT " + json.dumps(rep))
'''


def run(code: str, workdir: str, timeout: int = 90) -> dict:
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    script, stl, runner = wd / "code.py", wd / "out.stl", wd / "_runner.py"
    script.write_text(code)
    runner.write_text(CQ_RUNNER)
    if stl.exists():
        stl.unlink()
    t0 = time.time()
    try:
        p = subprocess.run([config.cadquery_python(), str(runner), str(script), str(stl)], cwd=str(wd),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace",
                           env=config.tool_env(OMP_NUM_THREADS="1"))
        m = re.search(r"CQ_REPORT (\{.*\})", p.stdout)
        rep = json.loads(m.group(1)) if m else {"status": "CRASH", "error": p.stdout[-800:]}
    except subprocess.TimeoutExpired:
        rep = {"status": "TIMEOUT", "error": f"timeout {timeout}s"}
    rep["latency_s"] = round(time.time() - t0, 1)
    rep["mesh"] = str(stl) if rep["status"] == "OK" and stl.exists() and stl.stat().st_size > 100 else None
    if rep["status"] == "OK" and rep["mesh"] is None:
        rep["status"] = "EMPTY"
    return rep
