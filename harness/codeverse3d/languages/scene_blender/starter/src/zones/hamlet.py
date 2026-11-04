"""src/zones/hamlet.py — zone "Hamlet": two gabled houses around a yard, lantern posts, a signpost
and a few crates.

CONTRACT: build(ctx) -> bpy.types.Collection — ctx.collection is this zone's; every PLACED thing
is ONE root object in it (a mesh, a collection-instance Empty), named <Name>_<i>, seated on
ctx.height_at.  Assets are collection instances of ctx.assets['<snake>'].
"""
import bmesh
import bpy
from env import linked, material, seat

SEED = 11
HOUSES = ((-2.0, 14.0, 7.0, 5.5, 2), (9.5, 15.0, 6.0, 5.0, 1))   # x, y, width, depth, floors


def _house(ctx, name, x, y, w, d, floors, wall, tile):
    """A box of `floors` storeys under a gabled roof: one mesh, two material slots, base on local z = 0."""
    h = 2.8 * floors
    bm = bmesh.new()
    body = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=(w, d, h), verts=body)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, h / 2), verts=body)
    rise, eave = 0.45 * w, 0.35
    v = [bm.verts.new(p) for p in ((-w / 2 - eave, -d / 2 - eave, h), (w / 2 + eave, -d / 2 - eave, h), (w / 2 + eave, d / 2 + eave, h),
                                   (-w / 2 - eave, d / 2 + eave, h), (0.0, -d / 2 - eave, h + rise), (0.0, d / 2 + eave, h + rise))]
    roof = [bm.faces.new(f) for f in ((v[0], v[1], v[4]), (v[2], v[3], v[5]), (v[1], v[2], v[5], v[4]), (v[3], v[0], v[4], v[5]))]
    for f in roof:
        f.material_index = 1
    ob = linked(ctx, name, bm, wall, tile)
    ob.location = (x, y, 0.0)
    return seat(ob, ctx.height_at)


def build(ctx):
    rng = ctx.rng(SEED)
    wall = material("HamletWall", (0.78, 0.72, 0.60), roughness=0.9, noise_scale=2.0, darker=0.8)
    tile = material("HamletTile", (0.42, 0.25, 0.20), roughness=0.85, noise_scale=4.0)
    timber = material("HamletTimber", (0.26, 0.18, 0.11), roughness=0.75, noise_scale=6.0)
    for i, (x, y, w, d, floors) in enumerate(HOUSES):
        _house(ctx, f"House_{i}", x, y, w, d, floors, wall, tile)

    bm = bmesh.new()                                                   # the signpost: a post and its board, one mesh
    post = bmesh.ops.create_cone(bm, segments=8, radius1=0.06, radius2=0.06, depth=2.3)["verts"]
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 1.15), verts=post)
    board = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=(1.6, 0.06, 0.45), verts=board)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 2.0), verts=board)
    sign = linked(ctx, "Signpost_0", bm, timber)
    sign.location = (10.0, 6.0, 0.0)
    seat(sign, ctx.height_at)

    def instance(source, name, x, y, yaw):                             # a collection-instance Empty = ONE placement row
        ob = bpy.data.objects.new(name, None)
        ob.instance_type, ob.instance_collection = "COLLECTION", source
        ob.location, ob.rotation_euler = (x, y, ctx.height_at(x, y)), (0.0, 0.0, yaw)
        ctx.collection.objects.link(ob)
        return ob

    lantern = ctx.assets.get("lantern_post")
    if lantern is not None:
        for i, (x, y) in enumerate(((-1.0, 4.5), (9.0, 3.5), (12.5, 10.5))):
            instance(lantern, f"LanternPost_{i}", x, y, 0.0)
    crate = ctx.assets.get("crate")
    if crate is not None:
        for i, (x, y) in enumerate(((3.4, 14.3), (5.2, 15.8), (13.4, 13.5))):
            instance(crate, f"Crate_{i}", x, y, rng.uniform(0.0, 1.5))
    return ctx.collection
