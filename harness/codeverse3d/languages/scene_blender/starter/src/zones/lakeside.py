"""src/zones/lakeside.py — zone "Lakeside": the lake in the env's basin, drifting slowly, and shore rocks.

CONTRACT: build(ctx) -> bpy.types.Collection — every placed thing is ONE root object seated on
ctx.height_at; the water is a water-named surface (the census files it as a backdrop and a foot
in it as on water).
"""
import bmesh
import bpy
from env import linked, material

SEED = 21
LAKE = (-24.0, 2.0)   # == env.BASIN's centre


def build(ctx):
    rng = ctx.rng(SEED)
    level = float(ctx.env.get("water_level", -0.3))

    water = bpy.data.materials.new("LakeWaterMat")               # a dielectric: transmission, not metal
    bsdf = water.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.10, 0.25, 0.29, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.05
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=16, y_segments=16, size=17.0)
    lake = linked(ctx, "LakeWater", bm, water)
    lake.location = (LAKE[0], LAKE[1], level)
    lake.keyframe_insert("location", frame=ctx.frame(0.0))        # the surface drifts 0.3 m between the judged frames
    lake.location.x += 0.3
    lake.keyframe_insert("location", frame=ctx.frame(1.5))
    action = lake.animation_data.action
    for fc in action.layers[0].strips[0].channelbag(action.slots[0]).fcurves:
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"

    rock = material("ShoreRock", (0.44, 0.42, 0.38), roughness=0.95, noise_scale=8.0)
    for i in range(10):                                            # irregular masses along the east bank, part sunk (a rock may)
        r = rng.uniform(0.4, 1.0)
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r)
        bmesh.ops.scale(bm, vec=(rng.uniform(0.8, 1.5), rng.uniform(0.7, 1.3), rng.uniform(0.5, 0.8)), verts=bm.verts)
        for v in bm.verts:
            v.co *= 1.0 + 0.12 * (rng.random() - 0.5)
        bmesh.ops.translate(bm, vec=(0.0, 0.0, -min(v.co.z for v in bm.verts)), verts=bm.verts)   # base on local z = 0
        ob = linked(ctx, f"Rock_{i}", bm, rock)
        x, y = LAKE[0] + 13.0 + (1.8 if i % 2 else -1.8) + rng.uniform(-0.3, 0.3), LAKE[1] - 9.0 + 2.2 * i   # spaced: no two share a box
        ob.location = (x, y, ctx.height_at(x, y) - 0.08 * r)   # its lowest vertex is under its centre: a little in, as a rock may
    return ctx.collection
