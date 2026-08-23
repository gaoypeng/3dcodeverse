"""Optional native Blender renderer (EEVEE / Cycles-CPU) for "native material" previews.

    blender -b --factory-startup --python render_bpy.py -- \
        (--glb object.glb | --script model.py) --out renders/ \
        [--views front_right_34:35:22,front:0:8,...] [--engine eevee|cycles] \
        [--size 768] [--samples 16]

Neutral 3-point light rig + shadow-catcher ground, camera framed from the world bbox
(azimuth 0 = front = -Y, CCW from above; elevation above the horizon).  Writes one PNG
per view and ``renders.json``.  The judge normally uses the three.js renderer; this
exists for previews of Blender-only material features.  Standalone: no ``codeverse``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import runpy
import sys
import time

DEFAULT_VIEWS = "front_right_34:35:22,back_left_34:215:22,front:0:8,top:0:88"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="render_bpy.py")
    p.add_argument("--glb")
    p.add_argument("--script")
    p.add_argument("--out", required=True)
    p.add_argument("--views", default=DEFAULT_VIEWS)
    p.add_argument("--engine", choices=("eevee", "cycles", "workbench"), default="eevee")
    p.add_argument("--size", type=int, default=768)
    p.add_argument("--samples", type=int, default=16)
    p.add_argument("--fov", type=float, default=40.0)
    a = p.parse_args(argv)
    if not a.glb and not a.script:
        p.error("one of --glb / --script is required")
    return a


def clear_scene(bpy) -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights, bpy.data.images):
        for item in list(block):
            block.remove(item)
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection


def load_subject(bpy, args) -> list:
    """Import the GLB or run the model script; return the subject objects."""
    if args.glb:
        bpy.ops.import_scene.gltf(filepath=os.path.abspath(args.glb))
    else:
        sys.argv = [args.script]
        runpy.run_path(os.path.abspath(args.script), run_name="__main__")
    subject = [o for o in bpy.context.scene.objects if o.type in ("MESH", "CURVE", "FONT", "SURFACE", "META", "EMPTY")]
    for o in list(bpy.context.scene.objects):  # the subject must not bring cameras/lights
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o, do_unlink=True)
    return subject


def scene_bounds(bpy, objects):
    from mathutils import Vector

    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    mins, maxs = [1e9] * 3, [-1e9] * 3
    for o in objects:
        if o.type == "EMPTY":
            continue
        oe = o.evaluated_get(dg)
        for c in oe.bound_box:
            w = oe.matrix_world @ Vector(c)
            for i in range(3):
                mins[i], maxs[i] = min(mins[i], w[i]), max(maxs[i], w[i])
    if mins[0] > maxs[0]:
        raise RuntimeError("nothing to render: no mesh geometry")
    return Vector(mins), Vector(maxs)


def setup_world_and_lights(bpy, center, radius) -> None:
    from mathutils import Vector

    scene = bpy.context.scene
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.92, 0.92, 0.92, 1.0)
        bg.inputs[1].default_value = 0.6
    # ground shadow catcher at the lowest point
    bpy.ops.mesh.primitive_plane_add(size=radius * 12, location=(center.x, center.y, 0.0))
    ground = bpy.context.object
    ground.name = "_Ground"
    ground.is_shadow_catcher = True
    gmat = bpy.data.materials.new("_GroundMat")
    gmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.85, 0.85, 0.85, 1)
    gmat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
    ground.data.materials.append(gmat)
    # 3-point area rig; energy scales with size (W for area lights)
    e = 40.0 * radius * radius
    for name, (ax, ay, az), energy, size in (
        ("_Key", (-1.0, -1.2, 1.6), e * 1.0, radius * 1.5),
        ("_Fill", (1.6, -0.8, 0.8), e * 0.4, radius * 2.5),
        ("_Rim", (0.4, 1.6, 1.2), e * 0.6, radius * 1.0),
    ):
        ldata = bpy.data.lights.new(name, "AREA")
        ldata.energy = energy
        ldata.size = size
        light = bpy.data.objects.new(name, ldata)
        bpy.context.collection.objects.link(light)
        light.location = center + Vector((ax, ay, az)) * radius * 2.2
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()


def setup_render(bpy, args) -> None:
    scene = bpy.context.scene
    r = scene.render
    r.resolution_x = r.resolution_y = args.size
    r.resolution_percentage = 100
    r.film_transparent = False
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "IMAGE"
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "Standard"
    if args.engine == "cycles":
        r.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = args.samples
        scene.cycles.use_denoising = True
    elif args.engine == "workbench":
        r.engine = "BLENDER_WORKBENCH"
    else:
        r.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in _engine_ids(bpy) else "BLENDER_EEVEE_NEXT"
        scene.eevee.taa_render_samples = max(4, args.samples)


def _engine_ids(bpy) -> set[str]:
    prop = bpy.types.RenderSettings.bl_rna.properties["engine"]
    return {e.identifier for e in prop.enum_items}


def place_camera(bpy, cam, center, radius, az_deg, el_deg, fov_deg) -> None:
    from mathutils import Vector

    az, el = math.radians(az_deg), math.radians(el_deg)
    dist = radius / math.sin(math.radians(fov_deg) / 2) * 1.15
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.location = center + d * dist
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start, cam.data.clip_end = dist * 0.01, dist * 10


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = parse_args(argv)
    import bpy

    t0 = time.monotonic()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    clear_scene(bpy)
    subject = load_subject(bpy, args)
    mn, mx = scene_bounds(bpy, subject)
    center = (mn + mx) / 2
    radius = max((mx - mn).length / 2, 1e-3)
    setup_world_and_lights(bpy, center, radius)
    setup_render(bpy, args)
    cam_data = bpy.data.cameras.new("_Cam")
    cam_data.angle = math.radians(args.fov)
    cam = bpy.data.objects.new("_Cam", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    views = []
    for spec in args.views.split(","):
        name, az, el = spec.split(":")
        place_camera(bpy, cam, center, radius, float(az), float(el), args.fov)
        path = os.path.join(out, f"view_{name}.png")
        bpy.context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        views.append({"name": name, "path": path, "azimuth_deg": float(az), "elevation_deg": float(el),
                      "camera_position": [round(v, 4) for v in cam.location], "look_at": [round(v, 4) for v in center]})
    manifest = {"views": views, "engine": bpy.context.scene.render.engine, "width": args.size, "height": args.size,
                "bbox_min": [round(v, 4) for v in mn], "bbox_max": [round(v, 4) for v in mx],
                "duration_ms": int((time.monotonic() - t0) * 1000)}
    with open(os.path.join(out, "renders.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
