"""Run a project's Blender-Python code, then render turntable views into renders/.

Invoked:  <blender> --background --factory-startup --python blender_render.py
              -- <code.py> <renders_dir> <mode:white|textured> <result.json>
Writes {status, error, images[]}. white = Workbench studio clay (transparent bg);
textured = EEVEE keeping the script's own materials + 3-point lighting.
Camera/lighting approach adapted from infinigen data_pipeline_operators/renderer.py.
"""

import json
import math
import os
import runpy
import shutil
import sys
import traceback

import bpy  # noqa: provided by Blender
from mathutils import Vector  # noqa

VIEWS = [("00", 0), ("01", 90), ("02", 180), ("03", 270)]   # turntable azimuths
RES = 512


def _write(p, r):
    with open(p, "w") as fh:
        json.dump(r, fh)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    code_path, renders_dir, mode, result_path = argv[0], argv[1], argv[2], argv[3]
    result = {"status": "ok", "error": "", "images": []}
    os.makedirs(renders_dir, exist_ok=True)

    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.node_groups):
        for it in list(coll):
            coll.remove(it)

    try:
        sys.argv = [code_path]
        runpy.run_path(code_path, run_name="__main__")
    except BaseException:
        tb = traceback.format_exc().strip().splitlines()
        result["status"] = "error"; result["error"] = (tb[-1] if tb else "unknown")[:300]
        return _write(result_path, result)

    # drop any camera/lights the script added; keep meshes
    for o in list(bpy.data.objects):
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o, do_unlink=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes or sum(len(o.data.vertices) for o in meshes if o.data) == 0:
        result["status"] = "no_geometry"
        return _write(result_path, result)

    mn = Vector((1e9,) * 3); mx = Vector((-1e9,) * 3)
    for o in meshes:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            mn = Vector(min(mn[i], w[i]) for i in range(3)); mx = Vector(max(mx[i], w[i]) for i in range(3))
    center = (mn + mx) / 2; size = mx - mn; extent = max(size) or 1.0
    dist = extent * 2.2

    scn = bpy.context.scene
    scn.render.resolution_x = scn.render.resolution_y = RES
    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGBA"
    cam_d = bpy.data.cameras.new("C"); cam = bpy.data.objects.new("C", cam_d)
    scn.collection.objects.link(cam); scn.camera = cam
    cam.data.lens = 50; cam.data.clip_end = extent * 25

    if mode == "white":
        scn.render.engine = "BLENDER_WORKBENCH"
        scn.render.film_transparent = True
        sh = scn.display.shading
        sh.light = "STUDIO"; sh.color_type = "SINGLE"; sh.single_color = (0.92, 0.92, 0.93)
        sh.show_cavity = False; sh.show_shadows = False; sh.show_object_outline = False
    else:  # textured: keep the script's materials; neutral white studio (per data_pipeline_operators/renderer_texture.py)
        for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scn.render.engine = eng; break
            except Exception:
                continue
        # neutral white world so material colour/roughness reads true-to-shader
        world = bpy.data.worlds.new("w"); scn.world = world; world.use_nodes = True
        nt = world.node_tree; nt.nodes.clear()
        bg = nt.nodes.new("ShaderNodeBackground"); wout = nt.nodes.new("ShaderNodeOutputWorld")
        bg.inputs[0].default_value = (1, 1, 1, 1); bg.inputs[1].default_value = 0.6
        nt.links.new(bg.outputs[0], wout.inputs[0])
        # 3-point area lights; energy scaled by extent^2 keeps illuminance constant across object sizes
        for name, loc, energy in [
            ("Key", (dist * 0.9, -dist * 0.7, dist * 1.5), 1400),
            ("Fill", (-dist * 0.7, -dist * 0.4, dist * 1.0), 500),
            ("Rim", (0.0, dist * 0.9, dist * 1.3), 800),
        ]:
            ld = bpy.data.lights.new(name, "AREA"); ld.energy = energy * (extent ** 2); ld.size = extent
            lo = bpy.data.objects.new(name, ld); scn.collection.objects.link(lo)
            lo.location = center + Vector(loc)

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
