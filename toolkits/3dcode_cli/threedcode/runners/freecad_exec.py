"""Run a FreeCAD Python project inside freecadcmd and report geometry / export STL.

Invoked:  FC_CODE=<user.py> FC_OUT=<out.json> [FC_STL=<stl_out>] freecadcmd freecad_exec.py
(args go via env because freecadcmd treats every positional arg as another script to run).
Writes {status, error, solids, volume} to FC_OUT; if FC_STL is set, tessellates the
largest-volume shape to STL for rendering. status: ok | no_geometry | error.
"""

import json
import os
import traceback


def main():
    code_path = os.environ["FC_CODE"]
    out_path = os.environ["FC_OUT"]
    stl_out = os.environ.get("FC_STL", "")
    result = {"status": "ok", "error": "", "solids": 0, "volume": 0.0}

    import FreeCAD as App  # provided by freecadcmd
    ns = {"__name__": "__main__", "App": App, "FreeCAD": App}
    try:
        import Part
        ns["Part"] = Part
    except Exception:
        pass
    try:
        with open(code_path) as fh:
            exec(compile(fh.read(), code_path, "exec"), ns)
    except BaseException:
        tb = traceback.format_exc().strip().splitlines()
        result["status"] = "error"
        result["error"] = (tb[-1] if tb else "error")[:300]
        json.dump(result, open(out_path, "w"))
        return

    # collect shapes with positive volume across all open documents
    shapes = []
    for doc in App.listDocuments().values():
        for obj in doc.Objects:
            shp = getattr(obj, "Shape", None)
            if shp is None:
                continue
            try:
                v = shp.Volume
                if v and v > 1e-9:
                    shapes.append((v, shp))
            except Exception:
                continue
    if not shapes:
        result["status"] = "no_geometry"
        json.dump(result, open(out_path, "w"))
        return

    shapes.sort(key=lambda x: -x[0])
    result["solids"] = len(shapes)
    result["volume"] = round(shapes[0][0], 3)
    if stl_out:
        try:
            import MeshPart
            shp = shapes[0][1]
            diag = getattr(shp.BoundBox, "DiagonalLength", 0) or 10.0
            lin = max(diag * 0.002, 1e-3)   # ~0.2% of size → curves stay smooth at any scale
            mesh = MeshPart.meshFromShape(Shape=shp, LinearDeflection=lin, AngularDeflection=0.2)
            mesh.write(stl_out)
        except Exception as e:
            result["stl_error"] = str(e)[:200]
    json.dump(result, open(out_path, "w"))


main()
