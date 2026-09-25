"""A hand-written scene_blender workspace with one of everything the census GLB must carry.

Blender frame (Z up, -Y front).  ``yard`` places four crates as collection instances — seated,
floating 1.5 m, sunk 0.6 m, and one tagged ``placement = 'free'`` — plus a parented cart (a mesh
with a child mesh: one placement row).  ``meadow`` scatters grass blades with geometry nodes (an
instance cloud).  ``broken`` raises on a known line.  ``CRATE_TRIS`` etc. are what bpy must count.
"""

from __future__ import annotations

from pathlib import Path

from codeverse3d.workspace import Workspace

SCENE = '''ZONES = {"yard": "Yard", "meadow": "Meadow", "broken": "Broken"}
ASSETS = {"crate": "Crate"}
HEROES = {}
CAMERAS = [{"name": "overview", "location": (30.0, -30.0, 18.0), "look_at": (0.0, 0.0, 0.0), "fov": 50}]
'''

ENV = '''import bmesh
import bpy


def height_at(x, y):
    return 0.0


def build_env(ctx):
    world = bpy.data.worlds.new("Sky")
    ctx.scene.world = world
    me = bpy.data.meshes.new("Ground")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=10, y_segments=10, size=40)
    bm.to_mesh(me)
    bm.free()
    ctx.collection.objects.link(bpy.data.objects.new("Ground", me))
    ctx.collection.objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN")))
    return {}
'''

CRATE = '''import bmesh
import bpy


def build_crate(ctx, variant=0):
    me = bpy.data.meshes.new("CrateBody")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.translate(bm, vec=(0, 0, 0.5), verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    ctx.collection.objects.link(bpy.data.objects.new("CrateBody", me))
    return ctx.collection
'''

YARD = '''import bpy


def place(ctx, name, x, y, z):
    e = bpy.data.objects.new(name, None)
    e.instance_type = "COLLECTION"
    e.instance_collection = ctx.assets["crate"]
    e.location = (x, y, z)
    ctx.collection.objects.link(e)
    return e


def build(ctx):
    place(ctx, "Crate_0", 0.0, 0.0, 0.0)
    place(ctx, "Crate_1", 4.0, 0.0, 1.5)
    place(ctx, "Crate_2", -4.0, 0.0, -0.6)
    bird = place(ctx, "Crate_3", 0.0, 5.0, 3.0)
    bird["placement"] = "free"
    bpy.ops.mesh.primitive_cube_add(size=1, location=(8, 0, 0.5))
    cart = bpy.context.object
    cart.name = "Cart_0"
    bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=0.2, location=(8, 0.6, 0.3))
    wheel = bpy.context.object
    wheel.name = "CartWheel_0"
    wheel.parent = cart
    wheel.matrix_parent_inverse = cart.matrix_world.inverted()
    return ctx.collection
'''

MEADOW = '''import bpy


def build(ctx):
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, -12, 0))
    patch = bpy.context.object
    patch.name = "GrassPatch"
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.05, depth=0.3, location=(0, 0, -50))
    blade = bpy.context.object
    blade.name = "Blade"
    blade.hide_render = True
    blade.hide_viewport = True
    ng = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    dist = n.new("GeometryNodeDistributePointsOnFaces")
    dist.inputs["Density"].default_value = 5.0
    inst = n.new("GeometryNodeInstanceOnPoints")
    info = n.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = blade
    join = n.new("GeometryNodeJoinGeometry")
    ng.links.new(gi.outputs[0], dist.inputs["Mesh"])
    ng.links.new(dist.outputs["Points"], inst.inputs["Points"])
    ng.links.new(info.outputs["Geometry"], inst.inputs["Instance"])
    ng.links.new(inst.outputs["Instances"], join.inputs["Geometry"])
    ng.links.new(gi.outputs[0], join.inputs["Geometry"])
    ng.links.new(join.outputs[0], go.inputs[0])
    patch.modifiers.new("Scatter", "NODES").node_group = ng
    return ctx.collection
'''

#: the line of ``broken.py`` that raises (after leaving a stray object in the scene root)
BROKEN_LINE = 7
BROKEN = '''import bpy


def build(ctx):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 12, 0.5))
    ctx.scene.collection.objects.link(bpy.data.objects.new("Stray", bpy.context.object.data))
    missing = {}["no_such_key"]
    return ctx.collection
'''


def write_fixture(ws: Workspace, *, broken: bool = True) -> Workspace:
    files = {"src/scene.py": SCENE, "src/env.py": ENV, "src/assets/crate.py": CRATE, "src/zones/yard.py": YARD,
             "src/zones/meadow.py": MEADOW}
    if broken:
        files["src/zones/broken.py"] = BROKEN
    else:
        files["src/scene.py"] = SCENE.replace(', "broken": "Broken"', "")
    for rel, text in files.items():
        p: Path = ws.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return ws
