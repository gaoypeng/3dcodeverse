"""Blender headless: render a GLB to PNG with an auto-framed 3/4 camera (workbench, solid shading).
usage: blender -b --factory-startup -noaudio --python eval/render_glb.py -- --glb in.glb --out out.png [--size 320]"""
import bpy, sys, math, argparse
from mathutils import Vector
argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser(); ap.add_argument("--glb"); ap.add_argument("--out"); ap.add_argument("--size", type=int, default=320); a = ap.parse_args(argv)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a.glb)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes: raise SystemExit("no mesh")
# bounds
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))); mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center = (mn + mx) / 2; radius = max((mx - mn).length / 2, 1e-3)
cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data); bpy.context.scene.collection.objects.link(cam)
direction = Vector((1.0, -1.2, 0.8)).normalized(); cam.location = center + direction * radius * 2.6
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler(); cam_data.lens = 40; cam_data.clip_end = radius * 100
bpy.context.scene.camera = cam
sc = bpy.context.scene; sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 32; sc.cycles.use_denoising = False
sc.render.resolution_x = a.size; sc.render.resolution_y = a.size; sc.render.film_transparent = False
w = bpy.data.worlds.new("w"); w.use_nodes = True; bg = w.node_tree.nodes["Background"]; bg.inputs[0].default_value = (0.92, 0.92, 0.92, 1); bg.inputs[1].default_value = 1.0; sc.world = w
sun_d = bpy.data.lights.new("sun", "SUN"); sun_d.energy = 3.0; sun = bpy.data.objects.new("sun", sun_d); sc.collection.objects.link(sun)
sun.rotation_euler = (math.radians(45), math.radians(20), math.radians(30))
import random; random.seed(0)
for o in meshes:
    mat = bpy.data.materials.new(f"m_{o.name}"); mat.use_nodes = True; bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf: bsdf.inputs["Base Color"].default_value = (random.uniform(0.25, 0.85), random.uniform(0.25, 0.85), random.uniform(0.25, 0.85), 1); bsdf.inputs["Roughness"].default_value = 0.6
    o.data.materials.clear(); o.data.materials.append(mat)
sc.render.filepath = a.out; bpy.ops.render.render(write_still=True)
