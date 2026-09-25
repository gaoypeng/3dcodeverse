"""A small built ``scene_blender``-shaped workspace for the Blender render tests (lane B).

Until the language's build wrapper exists this stands in for its outputs: ``artifacts/scene.blend``
(ground, a tower, a sphere that moves between t = 0 and t = 1.5 — or does not — a sun, two
authored cameras with ``c3d_look_at`` and ``scene["c3d_cameras"]``), the geometry as a GLB, a
host entry that loads it (the shape of the census-GLB adapter), and ``artifacts/census.json``
from the unchanged probe.  Blender frame: Z-up, -Y front; the GLB and every spec are Y-up.
"""

from __future__ import annotations

from pathlib import Path

from codeverse3d.config import get_settings
from codeverse3d.languages.blender import blender_env
from codeverse3d.proc import run_subprocess
from codeverse3d.workspace import Workspace

BUILD_BPY = r'''
import sys
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
blend, glb, moving = argv[0], argv[1], argv[2] == "1"
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.fps = 30
sc.frame_start, sc.frame_end = 1, 46


def mat(name, rgb):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1.0)
    return m


bpy.ops.mesh.primitive_plane_add(size=40)
g = bpy.context.object
g.name = "Ground"
g.data.materials.append(mat("GroundMat", (0.35, 0.33, 0.3)))
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
t = bpy.context.object
t.name = "Tower"
t.data.materials.append(mat("TowerMat", (0.8, 0.2, 0.1)))
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(-4, 0, 1))
s = bpy.context.object
s.name = "Mover"
s.data.materials.append(mat("MoverMat", (0.1, 0.3, 0.9)))
if moving:
    s.location.x = -4
    s.keyframe_insert("location", frame=1)
    s.location.x = 4
    s.keyframe_insert("location", frame=46)
sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 4.0
sun.rotation_euler = (0.7, 0.2, 0.5)
sc.collection.objects.link(sun)
w = bpy.data.worlds.new("World")
w.use_nodes = True
w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.4, 0.55, 0.8, 1.0)
w.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
sc.world = w
for name, pos in (("Front", (0, -14, 4)), ("Side", (14, 0, 4))):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    cam.data.sensor_fit = "VERTICAL"
    cam.data.angle_y = 0.7
    cam.location = pos
    cam["c3d_look_at"] = [0.0, 0.0, 1.0]
    from mathutils import Vector
    cam.rotation_euler = (Vector((0, 0, 1)) - Vector(pos)).to_track_quat("-Z", "Y").to_euler()
    sc.collection.objects.link(cam)
sc["c3d_cameras"] = ["Front", "Side"]
sc.camera = bpy.data.objects["Front"]
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", export_cameras=False, export_lights=False,
                          export_animations=False)
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
'''

#: the harness cameras the census GLB carries (``extras.c3d_cameras``, GLB frame — the shape
#: ``_census_glb._glb_camera`` writes): Blender (x, y, z) → GLB (x, z, -y), vertical fov 0.7 rad
GLB_CAMERAS = [{"name": "Front", "position": [0, 4, 14], "lookAt": [0, 1, 0], "fov": 40.107},
               {"name": "Side", "position": [14, 4, 0], "lookAt": [0, 1, 0], "fov": 40.107}]


def _with_scene_extras(glb: bytes, extras: dict) -> bytes:
    """``glb`` with ``extras`` on its first scene (the census writer's camera channel)."""
    import json
    import struct

    n = struct.unpack("<I", glb[12:16])[0]
    doc = json.loads(glb[20:20 + n])
    doc["scenes"][0]["extras"] = extras
    body = json.dumps(doc, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)
    rest = glb[20 + n:]
    return struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(body) + len(rest)) + struct.pack("<I4s", len(body), b"JSON") + body + rest


def build_fixture(ws: Workspace, *, moving: bool = True, probe: bool = True) -> Workspace:
    """Write the fixture into ``ws``: the .blend, its census GLB (cameras in the extras) and, with
    ``probe``, census.json from the JS probe of that GLB — the path ``SceneBlenderRuntime`` takes."""
    ws.artifacts.mkdir(parents=True, exist_ok=True)
    script = ws.root / "build_fixture.py"
    script.write_text(BUILD_BPY)
    glb = ws.root / "public" / "assets" / "scene.glb"
    glb.parent.mkdir(parents=True, exist_ok=True)
    blend = ws.artifacts / "scene.blend"
    blender = get_settings().resolve_blender()
    proc = run_subprocess([blender, "-b", "--factory-startup", "--python", str(script), "--",
                           str(blend), str(glb), "1" if moving else "0"], cwd=ws.root, env=blender_env(), timeout_s=120)
    assert proc.returncode == 0 and blend.is_file() and glb.is_file(), proc.stderr[-2000:]
    census_glb = ws.artifacts / "census.glb"
    census_glb.write_bytes(_with_scene_extras(glb.read_bytes(), {"c3d_cameras": GLB_CAMERAS}))
    if probe:
        from codeverse3d.spatial.probes import run_probe

        _, _, census = run_probe(ws, glb=census_glb, timeout_s=90)
        assert census, "the census-GLB probe produced no census"
        ws.write_json(ws.artifacts / "census.json", {**census, "language": "scene_blender"})
    return ws


def blender_available() -> bool:
    b = get_settings().resolve_blender()
    return bool(b) and Path(b).exists()
