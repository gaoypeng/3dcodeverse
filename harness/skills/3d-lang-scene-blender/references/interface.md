# Two modules the build runs as-is (the interface shown once; the content is a harbour, yours is the brief's)

`src/env.py`
```python
"""src/env.py — the environment of `Harbour at dusk`: world, sun, ground, atmosphere."""
import math
import bmesh
import bpy

SUN_AZIMUTH_DEG, SUN_ELEVATION_DEG = 250.0, 8.0   # the plan's light (compass 0 = +X, 90 = +Y); zones may `from env import` these
WORLD_M = 320.0                                   # how far this place's land and air reach (plan span 100 m)

def height_at(x, y):   # THE ground: any function of (x, y) the brief's landform needs
    return -2.0 * min(1.0, x / 25.0) if x >= 0.0 else 0.04 * max(0.0, -45.0 - x)

def linked(ctx, name, bm, mat):                   # code zones share lives here (`from env import linked`)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free(); me.materials.append(mat)
    ctx.collection.objects.link(ob := bpy.data.objects.new(name, me))
    return ob
```
```python
def build_env(ctx):
    ctx.scene.world = bpy.data.worlds.new("Dusk")   # REQUIRED: a world — its sky is the brief's
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))   # REQUIRED: a lamp; a Sun shines down its own -Z
    sun.rotation_euler = (math.radians(90.0 - SUN_ELEVATION_DEG), 0.0, math.radians(SUN_AZIMUTH_DEG + 90.0))
    ctx.collection.objects.link(sun)
    ground = bpy.data.materials.new("GroundMat")    # STAND-IN: one constant colour is graded flat_ground
    bm = bmesh.new(); bmesh.ops.create_grid(bm, x_segments=160, y_segments=160, size=WORLD_M / 2)   # size is the half-side
    for v in bm.verts: v.co.z = height_at(v.co.x, v.co.y)   # REQUIRED: the ground samples height_at
    linked(ctx, "Ground", bm, ground)
```
```python
    haze = bpy.data.materials.new("Haze"); nodes = haze.node_tree.nodes; nodes.remove(nodes["Principled BSDF"])
    vol = nodes.new("ShaderNodeVolumePrincipled"); vol.inputs["Density"].default_value = 1.0 / WORLD_M   # 1 / density >= span / 1.5
    haze.node_tree.links.new(vol.outputs["Volume"], nodes["Material Output"].inputs["Volume"])   # REQUIRED: an atmosphere, volume-only
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bmesh.ops.scale(bm, vec=(WORLD_M, WORLD_M, 30.0), verts=bm.verts)
    linked(ctx, "Atmosphere", bm, haze).location.z = 15.0
    return {"ground_material": ground, "water_level": 0.0}
```

`src/zones/quay.py`
```python
"""src/zones/quay.py — zone "Quay": the granite quay deck with its bollards."""
import bmesh, bpy
from env import linked

CENTRE, DECK_TOP, N_BOLLARDS = (-20.0, 0.0), 1.2, 6   # the plan's zone centre, deck height and count, in this frame

def build(ctx):
    rng = ctx.rng(11)
    granite = bpy.data.materials.new("QuayGranite")  # STAND-IN surface: the deck's granite is yours to wire
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bmesh.ops.scale(bm, vec=(40.0, 60.0, DECK_TOP), verts=bm.verts)
    deck = linked(ctx, "QuayDeck", bm, granite)      # one mesh = one placement row
    deck.location = (*CENTRE, ctx.height_at(*CENTRE) + DECK_TOP / 2)   # its lowest vertices on the (level) ground
    for i in range(N_BOLLARDS):                        # along the water edge, ON the deck
        copy = bpy.data.objects.new(f"Bollard_{i}", None)   # a collection-instance Empty = one placement row
        copy.instance_type, copy.instance_collection = "COLLECTION", ctx.assets["bollard"]
        copy.location = (CENTRE[0] + 18.0 + rng.uniform(-0.2, 0.2), CENTRE[1] - 25.0 + i * 10.0, DECK_TOP)   # the deck top is its ground
        copy.rotation_euler.z = rng.uniform(0.0, 6.283)
        ctx.collection.objects.link(copy)
    return ctx.collection
```
