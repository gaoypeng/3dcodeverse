"""Run a contributor's Blender-Python project INSIDE Blender and report the outcome.

Logic adapted from infinigen `data_pipeline_operators/validate_blender_scripts.py`.
Invoked as:  <blender> --background --factory-startup --python blender_exec.py -- <code.py> <out.json>
Writes {status, error, mesh_objects, verts, faces} to <out.json>. status:
  ok | no_geometry (ran but produced no mesh) | error (raised / non-zero exit).
"""

import json
import runpy
import sys
import traceback

import bpy  # noqa: provided by Blender


def _write(path, result):
    with open(path, "w") as fh:
        json.dump(result, fh)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    code_path, out_path = argv[0], argv[1]
    result = {"status": "ok", "error": "", "mesh_objects": 0, "verts": 0, "faces": 0, "material_slots": 0}

    # clean slate — robust even to scripts that don't clear the default scene themselves
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.node_groups):
        for item in list(coll):
            coll.remove(item)
    bpy.context.scene.cursor.location = (0, 0, 0)

    try:
        saved_argv = sys.argv[:]
        sys.argv = [code_path]                       # script sees clean argv
        runpy.run_path(code_path, run_name="__main__")
        sys.argv = saved_argv
    except SystemExit as e:
        if e.code not in (None, 0):
            result["status"] = "error"
            result["error"] = f"script called sys.exit({e.code})"
            _write(out_path, result)
            return
    except BaseException:
        tb = traceback.format_exc().strip().splitlines()
        result["status"] = "error"
        result["error"] = (tb[-1] if tb else "unknown")[:300]
        _write(out_path, result)
        return

    bpy.context.view_layer.update()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    result["mesh_objects"] = len(meshes)
    result["verts"] = sum(len(o.data.vertices) for o in meshes if o.data)
    result["faces"] = sum(len(o.data.polygons) for o in meshes if o.data)
    # material slots — a textured submission with geometry but no materials is suspect
    # (essence of data_pipeline_operators/validate_texture_material_slots.py)
    result["material_slots"] = sum(sum(1 for m in o.data.materials if m is not None)
                                   for o in meshes if o.data)
    if result["verts"] == 0:
        result["status"] = "no_geometry"
    _write(out_path, result)


main()
