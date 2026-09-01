"""Render a GLB (exported from a CadQuery Assembly) to N azimuth views on a
transparent background, GPU Cycles. Mirrors blender_urdf.py lighting/camera.
Usage: blender --background --threads 4 --python blender_glb.py -- <glb> <out_dir> [res] [samples] [n_azim] [material|clay]
"""
import bpy, sys, os, math
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
GLB, OUT = argv[0], argv[1]
RES = int(argv[2]) if len(argv) > 2 else 1024
SAMPLES = int(argv[3]) if len(argv) > 3 else 16
NAZ = int(argv[4]) if len(argv) > 4 else 8
MODE = argv[5] if len(argv) > 5 else "material"
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=GLB)
objs = [o for o in scene.objects if o.type == "MESH"]
if not objs:
    print("GLB_RENDER_EMPTY")
    sys.exit(0)

mn = Vector((1e18,) * 3); mx = Vector((-1e18,) * 3)
for ob in objs:
    for c in ob.bound_box:
        w = ob.matrix_world @ Vector(c)
        for k in range(3):
            mn[k] = min(mn[k], w[k]); mx[k] = max(mx[k], w[k])
center = (mn + mx) / 2; radius = max((mx - mn).length / 2, 1e-4)

CLAY = bpy.data.materials.new("clay"); CLAY.use_nodes = True
CLAY.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.82, 0.82, 0.84, 1.0)
CLAY.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.5
if MODE == "clay":
    for ob in objs:
        ob.data.materials.clear(); ob.data.materials.append(CLAY)

w = bpy.data.worlds.new("w"); scene.world = w; w.use_nodes = True
bg = w.node_tree.nodes["Background"]; bg.inputs[0].default_value = (1, 1, 1, 1); bg.inputs[1].default_value = 0.55
sd = bpy.data.lights.new("s", type="SUN"); sd.energy = 3.0; sd.angle = math.radians(8)
so = bpy.data.objects.new("s", sd); scene.collection.objects.link(so)
so.rotation_euler = (math.radians(52), math.radians(12), math.radians(-50))
FOV = math.radians(45)
cd = bpy.data.cameras.new("c"); cd.sensor_fit = "AUTO"; cd.angle = FOV
co = bpy.data.objects.new("c", cd); scene.collection.objects.link(co); scene.camera = co

scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "CUDA"; prefs.refresh_devices()
gpu = any(d.type == "CUDA" for d in prefs.devices)
for d in prefs.devices:
    d.use = (d.type == "CUDA")
scene.cycles.device = "GPU" if gpu else "CPU"
scene.cycles.samples = SAMPLES; scene.cycles.use_denoising = True
try:
    scene.cycles.denoiser = "OPENIMAGEDENOISE"; scene.cycles.denoising_use_gpu = True
except Exception:
    pass
scene.render.resolution_x = RES; scene.render.resolution_y = RES
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"; scene.render.image_settings.color_mode = "RGBA"

dist = radius / math.sin(FOV / 2) * 1.15
elev = math.radians(25)
for i in range(NAZ):
    a = math.radians(i * 360.0 / NAZ)
    eye = center + Vector((dist * math.cos(elev) * math.cos(a), dist * math.cos(elev) * math.sin(a), dist * math.sin(elev)))
    co.location = eye
    co.rotation_euler = (center - eye).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = os.path.join(OUT, "view_%02d.png" % i)
    bpy.ops.render.render(write_still=True)
print("GLB_RENDER_DONE objs=%d views=%d mode=%s" % (len(objs), NAZ, MODE))
