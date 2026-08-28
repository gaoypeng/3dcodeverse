"""
cq_check.py  -- per-model validation worker (run in a subprocess).

Executes a generated CadQuery script (without its __main__ export block),
computes exact volume + topological validity via OpenCASCADE, and exports the
STEP file for the corpus. Prints a single JSON line with the result.

Statuses: OK / INVALID / DEGENERATE / EXEC_FAIL / GEOM_FAIL / EXPORT_FAIL
"""
import sys
import os
import json
import runpy


def main():
    py_path, step_out = sys.argv[1], sys.argv[2]

    # 1. Execute script body (run_name != __main__ skips its export block)
    try:
        ns = runpy.run_path(py_path, run_name="_cqcheck_")
    except Exception as e:
        print(json.dumps({"status": "EXEC_FAIL",
                          "detail": f"{type(e).__name__}: {e}"[:300]}))
        return

    solid = ns.get("solid")
    if solid is None:
        print(json.dumps({"status": "EXEC_FAIL", "detail": "no `solid` variable"}))
        return

    # 2. OCC-native volume + validity
    try:
        shape = solid.val()
        from OCP.GProp import GProp_GProps
        from OCP.BRepGProp import BRepGProp
        from OCP.BRepCheck import BRepCheck_Analyzer
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape.wrapped, props)
        vol = abs(props.Mass())
        valid = bool(BRepCheck_Analyzer(shape.wrapped).IsValid())
    except Exception as e:
        print(json.dumps({"status": "GEOM_FAIL",
                          "detail": f"{type(e).__name__}: {e}"[:300]}))
        return

    if vol < 1e-9:
        print(json.dumps({"status": "DEGENERATE", "detail": f"volume={vol:.3e}"}))
        return

    # 3. Export STEP for the corpus
    try:
        import cadquery as cq
        os.makedirs(os.path.dirname(step_out), exist_ok=True)
        cq.exporters.export(solid, step_out)
    except Exception as e:
        print(json.dumps({"status": "EXPORT_FAIL",
                          "detail": str(e)[:200], "volume": vol}))
        return

    print(json.dumps({"status": "OK" if valid else "INVALID",
                      "detail": f"volume={vol:.4f}", "volume": vol}))


if __name__ == "__main__":
    main()
