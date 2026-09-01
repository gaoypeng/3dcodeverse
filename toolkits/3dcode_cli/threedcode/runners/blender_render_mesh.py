"""Render a MESH FILE (glb/gltf/stl) into renders_dir — clay turntable, no code run.

For dialects whose code doesn't execute in Blender (e.g. CadQuery exports a mesh first).
Invoked: <blender> --background --factory-startup --python blender_render_mesh.py
            -- <mesh_path> <renders_dir> <mode:white|textured> <result.json>
"""

import json
import math
import os
import shutil
import sys

import bpy  # noqa
from mathutils import Vector  # noqa

VIEWS = [("00", 0), ("01", 90), ("02", 180), ("03", 270)]
RES = 512


def _write(p, r):
    with open(p, "w") as fh:
        json.dump(r, fh)


def _import(mesh_path):
    ext = os.path.splitext(mesh_path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=mesh_path)
    elif ext == ".stl":
        try:
            bpy.ops.wm.stl_import(filepath=mesh_path)      # Blender 4.0+
        except AttributeError:
            bpy.ops.import_mesh.stl(filepath=mesh_path)    # older
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=mesh_path)
    else:
        raise ValueError(f"unsupported mesh format: {ext}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    mesh_path, renders_dir, mode, result_path = argv[0], argv[1], argv[2], argv[3]
    result = {"status": "ok", "error": "", "images": []}
    os.makedirs(renders_dir, exist_ok=True)

    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()
    try:
        _import(mesh_path)
    except Exception as e:
        result["status"] = "error"; result["error"] = str(e)[:300]
        return _write(result_path, result)

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes or sum(len(o.data.vertices) for o in meshes if o.data) == 0:
        result["status"] = "no_geometry"
        return _write(result_path, result)

    # auto-smooth: interpolate normals on curved surfaces (no facet ribbing) while keeping
    # sharp feature edges crisp — the right "CAD look" for tessellated B-rep exports.
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
    bpy.ops.object.shade_smooth()
    for op in ("shade_auto_smooth", "shade_smooth_by_angle"):
        try:
            getattr(bpy.ops.object, op)(angle=math.radians(35))
            break
        except Exception:
            continue

    mn = Vector((1e9,) * 3); mx = Vector((-1e9,) * 3)
    for o in meshes:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            mn = Vector(min(mn[i], w[i]) for i in range(3)); mx = Vector(max(mx[i], w[i]) for i in range(3))
    center = (mn + mx) / 2; size = mx - mn; extent = max(size) or 1.0
    dist = extent * 2.2

    scn = bpy.context.scene
    scn.render.resolution_x = scn.render.resolution_y = RES
    scn.render.image_settings.file_format = "PNG"; scn.render.image_settings.color_mode = "RGBA"
    cam_d = bpy.data.cameras.new("C"); cam = bpy.data.objects.new("C", cam_d)
    scn.collection.objects.link(cam); scn.camera = cam
    cam.data.lens = 50; cam.data.clip_end = extent * 25

    scn.render.engine = "BLENDER_WORKBENCH"
    scn.render.film_transparent = True
    sh = scn.display.shading
    sh.light = "STUDIO"; sh.color_type = "SINGLE"; sh.single_color = (0.92, 0.92, 0.93)
    sh.show_shadows = False
    sh.show_cavity = False          # cavity ribbons curved tessellation — let auto-smooth carry the shape
    sh.show_object_outline = True   # silhouette edge only → subtle CAD-drawing feel, no facet ribbing
    try:
        sh.object_outline_color = (0.15, 0.15, 0.17)
    except Exception:
        pass

    for tag, deg in VIEWS:
        rad = math.radians(deg)
        bx, by = 0.45, -1.0
        dirv = Vector((bx * math.cos(rad) - by * math.sin(rad),
                       bx * math.sin(rad) + by * math.cos(rad), 0.6)).normalized()
        cam.location = center + dirv * dist
        cam.rotation_euler = (center - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
        scn.render.filepath = os.path.join(renders_dir, f"view_{tag}.png")
        bpy.ops.render.render(write_still=True)
        result["images"].append(f"view_{tag}.png")
    shutil.copy(os.path.join(renders_dir, "view_00.png"), os.path.join(renders_dir, "thumb.png"))
    result["images"].append("thumb.png")
    _write(result_path, result)


main()
