"""Headless-Blender fallback renderer (Workbench, studio light) for a GLB.

Usage (run BY THE HARNESS, never by agent code)::

    blender -b --factory-startup --python render_glb_bpy.py -- spec.json

``spec.json``: ``{"glb", "out_dir", "width", "height", "views":[{"name","az","el"}]}``.
Azimuth 0 = front (camera on -Y looking +Y in Blender's Z-up world), CCW from
above; elevation in degrees above the horizon.  Writes ``out_dir/view_<name>.png``.
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _scene_bounds():
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        for c in ob.bound_box:
            w = ob.matrix_world @ Vector(c)
            lo = Vector(min(a, b) for a, b in zip(lo, w, strict=True))
            hi = Vector(max(a, b) for a, b in zip(hi, w, strict=True))
    if not math.isfinite(lo.x):
        lo, hi = Vector((-0.5,) * 3), Vector((0.5,) * 3)
    return lo, hi


def _look_at(cam, target: Vector) -> None:
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :]
    spec = json.loads(Path(argv[0]).read_text())
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_scene()
    bpy.ops.import_scene.gltf(filepath=spec["glb"])
    lo, hi = _scene_bounds()
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-3)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "MATERIAL"
    shading.show_cavity = True
    scene.display.render_aa = "8"
    scene.render.resolution_x = int(spec.get("width", 512))
    scene.render.resolution_y = int(spec.get("height", 512))
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("World")
    scene.world.color = (0.92, 0.92, 0.92)

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens_unit = "FOV"
    cam_data.angle = math.radians(40.0)
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    dist = radius / math.sin(cam_data.angle / 2) * 1.15

    for v in spec["views"]:
        az, el = math.radians(v["az"]), math.radians(v["el"])
        direction = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
        cam.location = center + direction * dist
        _look_at(cam, center)
        scene.render.filepath = str(out_dir / f"view_{v['name']}.png")
        bpy.ops.render.render(write_still=True)
        print(f"[render] {scene.render.filepath}")


main()
