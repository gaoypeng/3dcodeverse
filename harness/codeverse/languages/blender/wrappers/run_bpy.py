"""Blender build wrapper — executed BY Blender, never imported by the harness.

    blender -b --factory-startup --python run_bpy.py -- \
        --script /abs/src/model.py --out /abs/artifacts [--stl] [--blend] \
        [--rlimit-gb 12] [--tri-limit 600000] [--seed 0]

What it does (in order):
  1. caps the address space (RLIMIT_AS) so geometry bombs die in-process;
  2. empties the factory scene (Cube/Camera/Light + orphan data), sets metric units;
  3. seeds ``random`` / numpy / mathutils.noise;
  4. runs the agent script with ``runpy`` and maps any exception to a line of model.py;
  5. collects a census (evaluated tri counts, world bboxes, materials, parents);
  6. warns on leftovers (cameras, lights, touched render settings, visible cutters);
  7. exports ``object.glb`` (Y-up, +Z front) and optionally ``object.stl`` (Z-up);
  8. writes ``build.json`` + ``census.json`` atomically and exits 0.

Exit codes: 0 = reported (even if the script failed); 2 = wrapper bug (no report).
This file must stay standalone: Blender's python cannot import ``codeverse``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import runpy
import sys
import time
import traceback
from typing import Any

GLB_NAME = "object.glb"
STL_NAME = "object.stl"
BLEND_NAME = "object.blend"
EXPORT_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META", "EMPTY"}
DEFAULT_NAME_RE = re.compile(r"^(Cube|Cylinder|Sphere|Plane|Cone|Torus|Icosphere|Circle|Grid|Monkey|Mesh|Text|Curve)(\.\d+)?$")


# ----------------------------------------------------------------------------- args / io
def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_bpy.py")
    p.add_argument("--script", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--stl", action="store_true")
    p.add_argument("--blend", action="store_true")
    p.add_argument("--rlimit-gb", type=float, default=12.0)
    p.add_argument("--tri-limit", type=int, default=600_000)
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args(argv)


def write_json_atomic(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


def apply_rlimit(gb: float) -> str:
    """Cap virtual memory; returns a note for build.json."""
    if gb <= 0:
        return "rlimit disabled"
    try:
        import resource

        cap = int(gb * 1024**3)
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        lim = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
        resource.setrlimit(resource.RLIMIT_AS, (lim, hard))
        return f"RLIMIT_AS={lim // 1024**2} MB"
    except (ImportError, ValueError, OSError) as e:
        return f"rlimit not applied: {e}"


# ----------------------------------------------------------------------------- scene prep
def clear_scene(bpy: Any) -> None:
    """Remove every object + datablock of the factory scene; keep scene/world."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    for attr in ("meshes", "materials", "cameras", "lights", "curves", "images", "node_groups", "textures"):
        block = getattr(bpy.data, attr)
        for item in list(block):
            with contextlib.suppress(RuntimeError):  # data still used by a linked library
                block.remove(item)
    scene = bpy.context.scene
    # removing "Collection" leaves the view layer's active collection dangling → bpy.context.collection
    # would be None; point it back at the scene master collection so `.objects.link()` works.
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:
        from mathutils import noise

        noise.seed_set(seed)
    except (ImportError, AttributeError):
        pass


def render_fingerprint(bpy: Any) -> dict[str, Any]:
    r = bpy.context.scene.render
    return {
        "engine": r.engine,
        "resolution": [r.resolution_x, r.resolution_y],
        "filepath": r.filepath,
        "frame_range": [bpy.context.scene.frame_start, bpy.context.scene.frame_end],
    }


# ----------------------------------------------------------------------------- exec + error mapping
def map_exception(exc: BaseException, script_path: str) -> dict[str, Any]:
    """Traceback → {error_type, error_message, error_file, error_line, error_source, traceback}."""
    real = os.path.realpath(script_path)
    info: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error_message": str(exc) or type(exc).__name__,
        "error_file": "",
        "error_line": None,
        "error_source": "",
    }
    if isinstance(exc, SyntaxError) and exc.filename and os.path.realpath(exc.filename) == real:
        info.update(error_file=os.path.basename(script_path), error_line=exc.lineno, error_source=(exc.text or "").strip())
    else:
        for frame in reversed(traceback.extract_tb(exc.__traceback__)):
            if os.path.realpath(frame.filename) == real:
                info.update(error_file=os.path.basename(script_path), error_line=frame.lineno, error_source=(frame.line or "").strip())
                break
    if isinstance(exc, MemoryError):
        info["error_message"] = (
            "MemoryError: the script hit the memory cap — a geometry bomb (tiny remesh voxel_size, "
            "huge subdivision/array counts, nested loops creating millions of faces). Reduce it."
        )
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    info["traceback"] = tb[-6000:]
    return info


def run_script(script_path: str) -> dict[str, Any] | None:
    """Execute the agent script as ``__main__``; return error info or None."""
    script_dir = os.path.dirname(os.path.abspath(script_path))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    saved_argv = sys.argv
    sys.argv = [script_path]
    try:
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as e:  # sys.exit(0) in a script is not a failure
        if e.code not in (None, 0):
            return map_exception(e, script_path)
    except BaseException as e:  # noqa: BLE001 — every failure must be reported, never propagate
        return map_exception(e, script_path)
    finally:
        sys.argv = saved_argv
    return None


# ----------------------------------------------------------------------------- census
def world_bbox(obj_eval: Any, Vector: Any) -> tuple[list[float], list[float]] | None:
    corners = [obj_eval.matrix_world @ Vector(c) for c in obj_eval.bound_box]
    if not corners:
        return None
    mn = [min(c[i] for c in corners) for i in range(3)]
    mx = [max(c[i] for c in corners) for i in range(3)]
    return [round(v, 5) for v in mn], [round(v, 5) for v in mx]


def is_hidden(obj: Any) -> bool:
    try:
        return bool(obj.hide_render or obj.hide_viewport or obj.hide_get())
    except RuntimeError:  # object not in the view layer
        return True


def collect_census(bpy: Any, tri_limit: int) -> dict[str, Any]:
    from mathutils import Vector

    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    scene_objects = set(bpy.context.scene.objects.keys())
    boolean_operands: dict[str, list[str]] = {}
    objects: list[dict[str, Any]] = []
    total_tris = 0
    mins: list[list[float]] = []
    maxs: list[list[float]] = []
    for obj in bpy.data.objects:
        rec: dict[str, Any] = {
            "name": obj.name,
            "type": obj.type,
            "parent": obj.parent.name if obj.parent else None,
            "in_scene": obj.name in scene_objects,
            "hidden": is_hidden(obj),
            "materials": [s.material.name for s in obj.material_slots if s.material],
            "modifiers": [m.type for m in obj.modifiers],
            "location": [round(v, 5) for v in obj.matrix_world.translation],
        }
        for m in obj.modifiers:
            if m.type == "BOOLEAN" and getattr(m, "object", None) is not None:
                boolean_operands.setdefault(m.object.name, []).append(obj.name)
        if obj.type in ("MESH", "CURVE", "SURFACE", "FONT", "META"):
            try:
                ob_eval = obj.evaluated_get(dg)
                me = ob_eval.to_mesh()
                me.calc_loop_triangles()
                rec["tri_count"] = len(me.loop_triangles)
                rec["vert_count"] = len(me.vertices)
                rec["has_uv"] = bool(me.uv_layers)
                rec["has_vertex_colors"] = bool(me.color_attributes)
                bb = world_bbox(ob_eval, Vector)
                ob_eval.to_mesh_clear()
            except RuntimeError as e:
                rec["tri_count"] = 0
                rec["census_error"] = str(e)
                bb = None
            if bb:
                rec["bbox_min"], rec["bbox_max"] = bb
                if rec["in_scene"] and not rec["hidden"]:
                    mins.append(bb[0])
                    maxs.append(bb[1])
            if rec["in_scene"] and not rec["hidden"]:
                total_tris += rec["tri_count"]
        objects.append(rec)
    warnings: list[str] = []
    cams = [o.name for o in bpy.data.objects if o.type == "CAMERA"]
    lights = [o.name for o in bpy.data.objects if o.type == "LIGHT"]
    if cams:
        warnings.append(f"script created camera(s) {cams}: not exported; remove camera code (the harness owns cameras)")
    if lights:
        warnings.append(f"script created light(s) {lights}: not exported; remove light code (the harness owns lighting)")
    for cutter, users in boolean_operands.items():
        o = bpy.data.objects.get(cutter)
        if o is not None and not is_hidden(o) and o.name in scene_objects:
            warnings.append(
                f"object '{cutter}' is a boolean operand of {users} but is still visible and WILL be exported as "
                f"geometry; hide it: obj.hide_set(True); obj.hide_render = True  (or bpy.data.objects.remove(obj))"
            )
    visible = [o for o in objects if o["in_scene"] and not o["hidden"] and o["type"] == "MESH"]
    if not visible:
        warnings.append("no visible mesh objects in the scene — nothing to export")
    for o in visible:
        if not o["materials"]:
            warnings.append(f"mesh '{o['name']}' has no material (will export grey)")
        if DEFAULT_NAME_RE.match(o["name"]):
            warnings.append(f"mesh '{o['name']}' keeps a default primitive name; set obj.name = '<PartName>'")
    unlinked = [o["name"] for o in objects if o["type"] == "MESH" and not o["in_scene"]]
    if unlinked:
        warnings.append(f"mesh object(s) {unlinked} were created but never linked to the scene → not exported; "
                        "call bpy.context.collection.objects.link(obj)")
    if total_tris > tri_limit:
        warnings.append(f"triangle budget exceeded: {total_tris} > {tri_limit}")
    census = {
        "objects": objects,
        "n_mesh_objects": len(visible),
        "tri_count": total_tris,
        "tri_limit": tri_limit,
        "materials": [m.name for m in bpy.data.materials],
        "cameras": cams,
        "lights": lights,
        "warnings": warnings,
        "frame": "z_up_neg_y_front",
    }
    if mins:
        census["scene_bbox_min"] = [round(min(m[i] for m in mins), 5) for i in range(3)]
        census["scene_bbox_max"] = [round(max(m[i] for m in maxs), 5) for i in range(3)]
    return census


# ----------------------------------------------------------------------------- export
def select_exportables(bpy: Any) -> list[Any]:
    bpy.ops.object.select_all(action="DESELECT")
    chosen = []
    for obj in bpy.context.scene.objects:
        if obj.type in EXPORT_TYPES and not is_hidden(obj):
            with contextlib.suppress(RuntimeError):  # not in the view layer (excluded collection)
                obj.select_set(True)
                chosen.append(obj)
    if chosen:
        bpy.context.view_layer.objects.active = chosen[0]
    return chosen


def export_glb(bpy: Any, path: str) -> None:
    """Kwargs verified on Blender 5.0.1 (io_scene_gltf2)."""
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        use_visible=False,
        export_apply=True,
        export_yup=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_extras=False,
        export_normals=True,
        export_texcoords=True,
    )


def export_stl(bpy: Any, path: str) -> None:
    if hasattr(bpy.ops.wm, "stl_export"):  # Blender 4.2+ native exporter (Z-up, -Y... forward 'Y' default)
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, apply_modifiers=True, ascii_format=False, global_scale=1.0)
    else:  # legacy add-on
        bpy.ops.export_mesh.stl(filepath=path, use_selection=True, use_mesh_modifiers=True, ascii=False)


# ----------------------------------------------------------------------------- main
def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = parse_args(argv)
    import bpy  # noqa: PLC0415 — only available inside Blender

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    script = os.path.abspath(args.script)
    build_path = os.path.join(out_dir, "build.json")
    census_path = os.path.join(out_dir, "census.json")
    for stale in (build_path, census_path, os.path.join(out_dir, GLB_NAME), os.path.join(out_dir, STL_NAME)):
        if os.path.exists(stale):
            os.remove(stale)

    t0 = time.monotonic()
    report: dict[str, Any] = {
        "ok": False, "language": "blender", "error_type": "", "error_message": "", "error_file": "",
        "error_line": None, "error_source": "", "traceback": "", "warnings": [], "exported": {},
        "blender_version": bpy.app.version_string, "rlimit": apply_rlimit(args.rlimit_gb),
    }
    if not os.path.isfile(script):
        report.update(error_type="FileNotFoundError", error_message=f"script not found: {script}")
        write_json_atomic(build_path, report)
        return 0

    clear_scene(bpy)
    seed_everything(args.seed)
    render_before = render_fingerprint(bpy)

    t_exec = time.monotonic()
    err = run_script(script)
    report["exec_ms"] = int((time.monotonic() - t_exec) * 1000)
    if err:
        report.update(err)

    # the script may have left edit mode / odd selection states behind
    try:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError:
        pass

    census = collect_census(bpy, args.tri_limit)
    if render_fingerprint(bpy) != render_before:
        census["warnings"].append("script changed render settings (engine/resolution/filepath/frames): remove that code")
    report["warnings"] = census["warnings"]
    write_json_atomic(census_path, census)

    if not err and census["tri_count"] > args.tri_limit:
        report.update(
            error_type="TriangleBudgetExceeded",
            error_message=f"{census['tri_count']} triangles > limit {args.tri_limit}; lower subdivision levels / segment counts",
        )

    chosen = select_exportables(bpy)
    if chosen:
        glb = os.path.join(out_dir, GLB_NAME)
        try:
            export_glb(bpy, glb)
            report["exported"]["glb"] = glb
        except Exception as e:  # noqa: BLE001
            if not report["error_type"]:
                report.update(error_type="ExportError", error_message=f"glTF export failed: {e}")
            report["traceback"] = report["traceback"] or traceback.format_exc()[-4000:]
        if args.stl:
            stl = os.path.join(out_dir, STL_NAME)
            try:
                select_exportables(bpy)
                export_stl(bpy, stl)
                report["exported"]["stl"] = stl
            except Exception as e:  # noqa: BLE001
                report["warnings"].append(f"STL export failed: {e}")
        if args.blend:
            blend = os.path.join(out_dir, BLEND_NAME)
            try:
                bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True)
                report["exported"]["blend"] = blend
            except Exception as e:  # noqa: BLE001
                report["warnings"].append(f".blend save failed: {e}")
    elif not report["error_type"]:
        report.update(error_type="EmptyScene", error_message="the script created no visible mesh objects")

    report["ok"] = not report["error_type"] and "glb" in report["exported"]
    report["duration_ms"] = int((time.monotonic() - t0) * 1000)
    write_json_atomic(build_path, report)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # wrapper bug: no report, loud exit
        traceback.print_exc()
        code = 2
    sys.exit(code)
