"""Blender build wrapper — executed BY Blender, never imported by the harness.

    blender -b --factory-startup --python run_bpy.py -- \
        --script /abs/src/model.py --out /abs/artifacts [--stl] [--blend] \
        [--rlimit-gb 12] [--tri-limit 600000] [--seed 0]

What it does (in order):
  1. caps the address space (RLIMIT_AS) so geometry bombs die in-process;
  2. empties the factory scene (Cube/Camera/Light + orphan data), sets metric units;
  3. seeds ``random`` / numpy / mathutils.noise;
  4. puts ``<ws>/src`` on ``sys.path`` (so ``from parts.seat import build_seat`` and
     ``import parts.seat`` work), runs the agent's ``model.py`` with ``runpy`` and maps
     any exception to ``<file under src/>:<line>`` (model.py or a part file);
  5. collects a census (``_census.py`` next to this file: evaluated tri counts, world
     bboxes, materials, parents);
  6. warns on leftovers (cameras, lights, touched render settings, visible cutters);
  7. exports ``object.glb`` (Y-up, +Z front) and optionally ``object.stl`` (Z-up);
  8. writes ``build.json`` + ``census.json`` atomically and exits 0.

Exit codes: 0 = reported (even if the script failed); 2 = wrapper bug (no report).
This file must stay standalone: Blender's python cannot import ``codeverse``
(the only sibling import is ``_census``, resolved via this file's own directory).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import random
import runpy
import sys
import time
import traceback
from typing import Any

_WRAPPER_DIR = os.path.dirname(os.path.abspath(__file__))
if _WRAPPER_DIR not in sys.path:
    sys.path.insert(0, _WRAPPER_DIR)
from _census import collect_census, is_hidden  # noqa: E402  (sibling module, Blender-executed)

GLB_NAME = "object.glb"
STL_NAME = "object.stl"
BLEND_NAME = "object.blend"
EXPORT_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META", "EMPTY"}
PARTS_PKG = "parts"  # src/parts/<snake>.py → ``from parts.<snake> import build_<snake>``


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
def src_relative(path: str, src_dir: str) -> str | None:
    """``/ws/src/parts/leg.py`` → ``src/parts/leg.py`` when ``path`` lives under ``src_dir``; else None."""
    real_src = os.path.realpath(src_dir)
    real = os.path.realpath(path)
    if real == real_src or not real.startswith(real_src + os.sep):
        return None
    return os.path.join(os.path.basename(real_src), os.path.relpath(real, real_src)).replace(os.sep, "/")


def import_hint(exc: BaseException, src_dir: str) -> str:
    """Extra guidance for the two import mistakes a multi-file model makes."""
    msg = str(exc)
    if isinstance(exc, ModuleNotFoundError) and (f"'{PARTS_PKG}." in msg or f"'{PARTS_PKG}'" in msg):
        mod = msg.split("'")[1].split(".")[-1]
        return f" — create {os.path.basename(src_dir)}/{PARTS_PKG}/{mod}.py (snake_case file name) or fix the import"
    if isinstance(exc, ImportError) and "cannot import name" in msg:
        name = msg.split("'")[1]
        where = msg.split("'")[3] if msg.count("'") >= 4 else PARTS_PKG
        return f" — define `def {name}():` in {where.replace('.', '/')}.py (every part file exports build_<snake>)"
    return ""


def map_exception(exc: BaseException, script_path: str) -> dict[str, Any]:
    """Traceback → {error_type, error_message, error_file, error_line, error_source, traceback}.

    ``error_file`` is workspace-relative (``src/model.py``, ``src/parts/leg.py``): the
    innermost traceback frame inside ``src/`` wins, so a failure in a part file is
    reported against that file, not against the ``from parts.x import …`` line.
    """
    src_dir = os.path.dirname(os.path.abspath(script_path))
    info: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error_message": (str(exc) or type(exc).__name__) + import_hint(exc, src_dir),
        "error_file": "",
        "error_line": None,
        "error_source": "",
    }
    syntax_rel = src_relative(exc.filename, src_dir) if isinstance(exc, SyntaxError) and exc.filename else None
    if syntax_rel:
        info.update(error_file=syntax_rel, error_line=exc.lineno, error_source=(exc.text or "").strip())
    else:
        for frame in reversed(traceback.extract_tb(exc.__traceback__)):
            rel = src_relative(frame.filename, src_dir)
            if rel:
                info.update(error_file=rel, error_line=frame.lineno, error_source=(frame.line or "").strip())
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
    """Execute the agent's entry script as ``__main__``; return error info or None.

    ``<ws>/src`` goes first on ``sys.path`` so the entry can import its part modules
    (``src/parts/<snake>.py``) either way: ``from parts.leg import build_leg`` or
    ``import parts.leg``.  A single-file ``model.py`` keeps working unchanged.
    """
    src_dir = os.path.dirname(os.path.abspath(script_path))
    if _WRAPPER_DIR in sys.path:  # the agent's code must not see the wrapper's helpers
        sys.path.remove(_WRAPPER_DIR)
    sys.path[:] = [p for p in sys.path if os.path.realpath(p) != os.path.realpath(src_dir)]
    sys.path.insert(0, src_dir)
    sys.dont_write_bytecode = True  # no __pycache__ inside the agent's git-tracked src/
    for name in [m for m in sys.modules if m == PARTS_PKG or m.startswith(PARTS_PKG + ".")]:
        del sys.modules[name]  # never reuse a stale module from somewhere else on the path
    importlib.invalidate_caches()
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


# ----------------------------------------------------------------------------- export
def select_exportables(bpy: Any) -> list[Any]:
    bpy.ops.object.select_all(action="DESELECT")
    chosen = []
    vl_names = set(bpy.context.view_layer.objects.keys())  # same predicate as the census
    for obj in bpy.context.scene.objects:
        if obj.type in EXPORT_TYPES and not is_hidden(obj, vl_names):
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
        # a keyframed object becomes a glTF clip the scene loops (scene heroes, 2026-09-07);
        # an object nobody keyframed exports exactly as before
        export_animations=True,
        export_frame_range=True,
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
