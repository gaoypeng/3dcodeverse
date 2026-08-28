"""
cq_check_batch.py -- batch validation worker.
Imports cadquery ONCE, then processes many models from a manifest file
(one "py_path<TAB>step_out" per line), printing one JSON line per model.

This amortizes the ~1.5s cadquery import over the whole batch instead of
paying it per model. Per-model SIGALRM timeout guards against hangs.
A hard crash (OCC segfault) kills the process; the driver detects the
missing tail of output and retries those models in isolation.
"""
import sys
import os
import json
import signal
import runpy


class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


signal.signal(signal.SIGALRM, _alarm)

# Import heavy deps once.
import cadquery as cq  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.BRepCheck import BRepCheck_Analyzer  # noqa: E402


def check_one(py_path, step_out, timeout=25):
    try:
        signal.alarm(timeout)
        ns = runpy.run_path(py_path, run_name="_cqcheck_")
        solid = ns.get("solid")
        if solid is None:
            signal.alarm(0)
            return "EXEC_FAIL", "no `solid` variable"
        shape = solid.val()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape.wrapped, props)
        vol = abs(props.Mass())
        if vol < 1e-9:
            signal.alarm(0)
            return "DEGENERATE", f"volume={vol:.3e}"
        valid = bool(BRepCheck_Analyzer(shape.wrapped).IsValid())
        os.makedirs(os.path.dirname(step_out), exist_ok=True)
        cq.exporters.export(solid, step_out)
        signal.alarm(0)
        return ("OK" if valid else "INVALID"), f"volume={vol:.4f}"
    except _Timeout:
        return "TIMEOUT", ""
    except Exception as e:
        signal.alarm(0)
        return "EXEC_FAIL", f"{type(e).__name__}: {e}"[:200]


def main():
    manifest = sys.argv[1]
    for line in open(manifest):
        line = line.rstrip("\n")
        if not line:
            continue
        py_path, step_out = line.split("\t")
        sub = os.path.basename(os.path.dirname(py_path))
        name = os.path.splitext(os.path.basename(py_path))[0]
        status, detail = check_one(py_path, step_out)
        print(json.dumps({"id": f"{sub}/{name}", "status": status, "detail": detail}),
              flush=True)


if __name__ == "__main__":
    main()
