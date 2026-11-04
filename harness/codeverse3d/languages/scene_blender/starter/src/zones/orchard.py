"""src/zones/orchard.py — zone "Orchard": a stand of apple trees and harvest crates (instances of
the crate asset).

CONTRACT: build(ctx) -> bpy.types.Collection — every placed thing is ONE root object seated on
ctx.height_at.
"""
import bmesh
import bpy
from env import linked, material, seat

SEED = 31
CENTER = (20.0, -12.0)


def _tree(ctx, name, x, y, height, bark, leaves, rng):
    """A trunk under two overlapping crown lobes: one mesh, two material slots, base on local z = 0."""
    bm = bmesh.new()
    trunk = bmesh.ops.create_cone(bm, segments=8, radius1=0.16, radius2=0.10, depth=height * 0.5)["verts"]
    bmesh.ops.translate(bm, vec=(0.0, 0.0, height * 0.25), verts=trunk)
    for dx, dy, dz, r in ((0.0, 0.0, height * 0.68, height * 0.32), (rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6), height * 0.8, height * 0.24)):
        crown = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r)["verts"]
        bmesh.ops.scale(bm, vec=(1.15, 1.0, 0.85), verts=crown)
        bmesh.ops.translate(bm, vec=(dx, dy, dz), verts=crown)
        for f in {f for v in crown for f in v.link_faces}:
            f.material_index = 1
    ob = linked(ctx, name, bm, bark, leaves)
    ob.location = (x, y, 0.0)
    ob.rotation_euler.z = rng.uniform(0.0, 6.28)
    return seat(ob, ctx.height_at)


def build(ctx):
    rng = ctx.rng(SEED)
    bark = material("OrchardBark", (0.29, 0.21, 0.14), roughness=0.9, noise_scale=6.0)
    leaves = material("OrchardLeaf", (0.31, 0.48, 0.18), roughness=0.7, noise_scale=10.0, darker=0.5)
    i = 0
    for row in range(3):                                           # the planted grid, jittered a little
        for col in range(4):
            x = CENTER[0] - 6.75 + 4.5 * col + rng.uniform(-0.3, 0.3)
            y = CENTER[1] - 4.5 + 4.5 * row + rng.uniform(-0.3, 0.3)
            _tree(ctx, f"AppleTree_{i}", x, y, rng.uniform(3.8, 5.0), bark, leaves, rng)
            i += 1

    crate = ctx.assets.get("crate")
    if crate is not None:
        for i, (x, y) in enumerate(((25.0, -21.0), (27.2, -19.4), (29.0, -21.6))):
            ob = bpy.data.objects.new(f"HarvestCrate_{i}", None)     # a collection-instance Empty = ONE placement row
            ob.instance_type, ob.instance_collection = "COLLECTION", crate
            ob.location, ob.rotation_euler = (x, y, ctx.height_at(x, y)), (0.0, 0.0, rng.uniform(0.0, 1.5))
            ctx.collection.objects.link(ob)
    return ctx.collection
