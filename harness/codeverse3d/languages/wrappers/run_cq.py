"""CadQuery build wrapper — run by the harness in a subprocess, never imported.

    python run_cq.py --script /abs/src/model.py --out /abs/artifacts \
        [--rlimit-gb 8] [--tri-limit 600000] [--tolerance 0.001] [--angular-tolerance 0.15] [--seed 0]

Steps: RLIMIT_AS → seed → exec model.py with traceback→line mapping (``_wrapper_common``,
shared with the Blender wrappers) → read the module-level ``result`` (cq.Assembly
preferred, or Workplane / Shape) → tessellate each part (world location applied) →
``object.glb`` (Y-up, +Z front, named nodes, PBR base colour from cq.Color) +
``object.step`` + ``object.stl`` → ``census.json`` + ``build.json`` (atomic).  Exit 0
whenever a report was written; 2 on wrapper bugs.  Standalone: imports cadquery / numpy /
trimesh and its sibling ``_wrapper_common`` only (never ``codeverse3d``).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from _wrapper_common import (  # noqa: E402  (sibling module)
    exit_after,
    map_exception,
    new_report,
    run_script,
    seed_everything,
    src_relative,
    wrapper_argv,
    write_report,
)

GLB_NAME, STEP_NAME, STL_NAME = "object.glb", "object.step", "object.stl"
PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:_\d+)?$")
DEFAULT_RGBA = (0.7, 0.7, 0.7, 1.0)
MEMORY_HINT = "reduce boolean/pattern counts or tessellation density."


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_cq.py")
    p.add_argument("--script", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rlimit-gb", type=float, default=8.0)
    p.add_argument("--tri-limit", type=int, default=600_000)
    p.add_argument("--tolerance", type=float, default=0.001)
    p.add_argument("--angular-tolerance", type=float, default=0.15)
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args(argv)


def entry_relative(script_path: str) -> str:
    """``src/model.py`` for the entry script itself."""
    return src_relative(script_path, os.path.dirname(os.path.abspath(script_path))) or os.path.basename(script_path)


def occ_hint(exc: BaseException) -> str:
    """What an OCC kernel refusal usually means, appended to its message."""
    if "BRep_API: command not done" in str(exc) or "StdFail_NotDone" in type(exc).__name__:
        return (" — OCC kernel refused the operation: fillet/chamfer radius >= adjacent wall/face size, conflicting "
                "adjacent fillets, or degenerate geometry. Clamp radii (< 0.45 x min thickness), select fewer edges, "
                "fillet before booleans.")
    return ""


def script_error(exc: BaseException, script: str) -> dict[str, Any]:
    return map_exception(exc, script, hint=occ_hint(exc), memory=MEMORY_HINT)


# ----------------------------------------------------------------------------- parts
def _as_shape(cq: Any, obj: Any, warnings: list[str] | None = None, label: str = "result") -> Any:
    """Workplane / Shape → one Shape (compound when several solids).

    A Workplane whose stack holds only Faces/Edges/Wires/Vertices (a trailing selector such
    as ``.faces(">Z")``) is NOT exported as a sheet: the solid it was selected from is used
    (``findSolid``) with a warning, or an error names the stack types when there is none.
    """
    if isinstance(obj, cq.Workplane):
        vals = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise ValueError(f"{label}: Workplane holds no shapes (did you forget .extrude()/.box()?)")
        if not any(isinstance(v, (cq.Solid, cq.Compound, cq.Shell)) for v in vals):
            kinds = sorted({type(v).__name__ for v in vals})
            try:
                solid = obj.findSolid(searchStack=True, searchParents=True)
            except Exception as e:  # noqa: BLE001 — ValueError from cadquery; anything else is equally "no solid"
                raise ValueError(f"{label}: Workplane stack holds only {'/'.join(kinds)} objects, not a Solid — "
                                 f"end the chain with a solid (.extrude()/.box()/.revolve(); drop the trailing selector) [{e}]") from e
            if warnings is not None:
                warnings.append(f"{label}: Workplane stack held {'/'.join(kinds)} objects (trailing selector) — exported the parent "
                                "solid instead; end the chain with the solid (drop the selector or call .end())")
            return solid
        return vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    if isinstance(obj, cq.Shape):
        return obj
    raise TypeError(f"{label}: unsupported object of type {type(obj).__name__}; expected cq.Workplane or cq.Shape")


def flatten_assembly(cq: Any, assy: Any, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    """Depth-first leaves: {name, shape (world-located), rgba, path}."""
    out: list[dict[str, Any]] = []

    def walk(node: Any, parent_loc: Any, parent_rgba: Any, path: list[str]) -> None:
        loc = parent_loc * node.loc
        rgba = node.color.toTuple() if node.color is not None else parent_rgba
        if node.obj is not None:
            shape = _as_shape(cq, node.obj, warnings, f"part {node.name!r}").moved(loc)
            out.append({"name": node.name, "shape": shape, "rgba": rgba, "path": "/".join(path + [node.name])})
        for child in node.children:
            walk(child, loc, rgba, path + [node.name])

    walk(assy, cq.Location(), None, [])
    return out


def collect_parts(cq: Any, result: Any, warnings: list[str] | None = None) -> tuple[list[dict[str, Any]], str]:
    if isinstance(result, cq.Assembly):
        return flatten_assembly(cq, result, warnings), "assembly"
    shape = _as_shape(cq, result, warnings, "result")
    return [{"name": "Object", "shape": shape, "rgba": None, "path": "Object"}], "single"


def tessellate(part: dict[str, Any], tol: float, ang: float) -> tuple[list[list[float]], list[list[int]]]:
    verts, tris = part["shape"].tessellate(tol, ang)
    return [list(v.toTuple()) for v in verts], [list(t) for t in tris]


def part_census(part: dict[str, Any], n_tris: int) -> dict[str, Any]:
    shape = part["shape"]
    bb = shape.BoundingBox()
    rec: dict[str, Any] = {
        "name": part["name"], "path": part["path"], "tri_count": n_tris,
        "bbox_min": [round(bb.xmin, 5), round(bb.ymin, 5), round(bb.zmin, 5)],
        "bbox_max": [round(bb.xmax, 5), round(bb.ymax, 5), round(bb.zmax, 5)],
        "rgba": [round(c, 4) for c in part["rgba"]] if part["rgba"] else None,
    }
    try:
        rec["volume_m3"] = round(float(shape.Volume()), 9)
        rec["n_solids"] = len(shape.Solids())
        rec["valid"] = bool(shape.isValid())
    except Exception as e:  # noqa: BLE001 — census must not kill the build
        rec["census_error"] = str(e)
    return rec


# ----------------------------------------------------------------------------- export
def build_glb(parts_meshes: list[tuple[dict[str, Any], list[list[float]], list[list[int]]]], path: str) -> int:
    """trimesh Scene with one named node per part; Z-up → Y-up (x, y, z) → (x, z, -y)."""
    import numpy as np
    import trimesh

    scene = trimesh.Scene()
    total = 0
    for part, verts, tris in parts_meshes:
        if not tris:
            continue
        v = np.asarray(verts, dtype=np.float64)
        v = np.column_stack([v[:, 0], v[:, 2], -v[:, 1]])
        mesh = trimesh.Trimesh(vertices=v, faces=np.asarray(tris, dtype=np.int64), process=False)
        rgba = part["rgba"] or DEFAULT_RGBA
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                name=f"{part['name']}Mat", baseColorFactor=[float(c) for c in rgba[:4]],
                metallicFactor=0.0, roughnessFactor=0.6))
        scene.add_geometry(mesh, node_name=part["name"], geom_name=part["name"])
        total += len(tris)
    if total == 0:
        raise ValueError("no triangles to export")
    with open(path, "wb") as fh:
        fh.write(scene.export(file_type="glb"))
    return total


def export_step_stl(cq: Any, result: Any, parts: list[dict[str, Any]], out_dir: str, tol: float, ang: float,
                    report: dict[str, Any]) -> None:
    step, stl = os.path.join(out_dir, STEP_NAME), os.path.join(out_dir, STL_NAME)
    try:
        if isinstance(result, cq.Assembly):
            result.export(step) if hasattr(result, "export") else result.save(step)
        else:
            cq.exporters.export(_as_shape(cq, result, None, "result"), step)
        report["exported"]["step"] = step
    except Exception as e:  # noqa: BLE001
        report["warnings"].append(f"STEP export failed: {e}")
    try:
        compound = cq.Compound.makeCompound([p["shape"] for p in parts])
        cq.exporters.export(compound, stl, tolerance=tol, angularTolerance=ang)
        report["exported"]["stl"] = stl
    except Exception as e:  # noqa: BLE001
        report["warnings"].append(f"STL export failed: {e}")


# ----------------------------------------------------------------------------- main
def main() -> int:
    args = parse_args(wrapper_argv())
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.monotonic()
    report = new_report(args.rlimit_gb)
    seed_everything(args.seed)
    import cadquery as cq  # noqa: PLC0415 — after the rlimit, before the script

    script = os.path.abspath(args.script)
    err: dict[str, Any] | None = None
    ns: dict[str, Any] | None = {}
    if not os.path.isfile(script):
        err = {"error_type": "FileNotFoundError", "error_message": f"script not found: {script}"}
    else:
        t_exec = time.monotonic()
        exc, ns = run_script(script)
        report["exec_ms"] = int((time.monotonic() - t_exec) * 1000)
        if exc is not None:
            err = script_error(exc, script)
        elif ns is None:
            err = {"error_type": "SystemExit", "error_file": entry_relative(script),
                   "error_message": "script called sys.exit() before `result` could be read; remove it"}
    census: dict[str, Any] = {"parts": [], "tri_count": 0, "warnings": report["warnings"], "frame": "z_up_neg_y_front"}
    if err is None and ns is not None and "result" not in ns:
        err = {"error_type": "MissingResult", "error_file": entry_relative(script),
               "error_message": "model.py must assign a module-level `result` (cq.Assembly or cq.Workplane); "
                                "it must exist at import time, not only under `if __name__ == '__main__':`"}
    if err is None and ns is not None:
        try:
            parts, kind = collect_parts(cq, ns["result"], report["warnings"])
            census["result_kind"] = kind
            if kind == "single":
                report["warnings"].append("result is a bare Workplane/Shape: exported as ONE node 'Object' — prefer cq.Assembly with named, coloured parts")
            meshes = []
            for p in parts:
                verts, tris = tessellate(p, args.tolerance, args.angular_tolerance)
                meshes.append((p, verts, tris))
                rec = part_census(p, len(tris))
                census["parts"].append(rec)
                if not PASCAL_RE.match(p["name"]):
                    report["warnings"].append(f"part name {p['name']!r} is not PascalCase (give every .add(...) a name='PartName')")
                if not tris:
                    report["warnings"].append(f"part {p['name']!r} tessellated to 0 triangles (empty/degenerate shape)")
                elif rec.get("n_solids") == 0:
                    report["warnings"].append(f"part {p['name']!r} contains no solid (a face/shell/wire set, volume 0) — "
                                              "build it as a solid (.extrude()/.box()/.shell()) so it has thickness")
            census["tri_count"] = sum(len(m[2]) for m in meshes)
            if census["parts"]:
                census["scene_bbox_min"] = [min(p["bbox_min"][i] for p in census["parts"]) for i in range(3)]
                census["scene_bbox_max"] = [max(p["bbox_max"][i] for p in census["parts"]) for i in range(3)]
            if census["tri_count"] > args.tri_limit:
                err = {"error_type": "TriangleBudgetExceeded",
                       "error_message": f"{census['tri_count']} triangles > limit {args.tri_limit}; simplify (fewer pattern copies / coarser curves)"}
            glb = os.path.join(out_dir, GLB_NAME)
            build_glb(meshes, glb)
            report["exported"]["glb"] = glb
            export_step_stl(cq, ns["result"], parts, out_dir, args.tolerance, args.angular_tolerance, report)
        except Exception as e:  # noqa: BLE001 — export-time failure in agent geometry
            err = script_error(e, script)
            if not err["error_file"]:
                err["error_type"], err["error_message"] = "ExportError", f"{type(e).__name__}: {e}"
    if err:
        report.update({k: v for k, v in err.items() if k in report})
    census["n_parts"] = len(census["parts"])
    report["ok"] = not report["error_type"] and "glb" in report["exported"]
    write_report(out_dir, report, census, t0)
    return 0


if __name__ == "__main__":
    exit_after(main)
