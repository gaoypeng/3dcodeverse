"""src/zones/hamlet.py — zone "Hamlet": two half-timbered houses around a paved yard, lantern
posts, a signpost and a few villagers.

CONTRACT: build(ctx) -> bpy.types.Collection — ctx.collection is this zone's; every PLACED thing
is ONE root object in it (a Batch root, an instance Empty), named <Name>_<i>, seated on
ctx.height_at.  Assets are collection instances of ctx.assets['<snake>'] (lib.scatter.instance).
"""
from lib import architecture, figure, materials, mesh, place, scatter

SEED = 11
HOUSES = ((-2.0, 14.0, 7.0, 5.5, 2), (9.5, 15.0, 6.0, 5.0, 1))   # x, y, width, depth, floors
YARD = (4.0, 7.0)


def build(ctx):
    rng = ctx.rng(SEED)
    h = ctx.height_at
    wall = materials.limestone("HamletWall", "D8CDB4")
    timber = materials.woodgrain(materials.material("HamletTimber", "4A3220", roughness=0.75))
    tile = materials.material("HamletTile", "7A4A3A", roughness=0.85, noise_scale=4.0)
    pane = materials.glass("HamletPane", "DDE6EE", roughness=0.1)
    door = materials.chipped_paint(materials.material("HamletDoor", "2F5F8F", roughness=0.5), "2F5F8F", "6B5A48")
    stones = [materials.limestone("YardStone", "A79F8E", scale=3.0), materials.limestone("YardStoneDark", "8E867A", scale=3.0)]
    mats = {"wall": wall, "timber": timber, "door": door, "window": pane}
    roofs = {"tile": tile, "trim": timber}

    for i, (x, y, w, d, floors) in enumerate(HOUSES):
        b = mesh.Batch(place.unique_name("House", i))
        architecture.timber_house(b, roofs, x, y, w, d, floors, mats, rng, i)
        root = b.finish(ctx.collection)[0]
        place.seat(root, h)                       # built at z = 0; the ground may not be exactly there

    yard = mesh.Batch("Yard_0")
    architecture.paving(yard, stones, center=(YARD[0], YARD[1], h(*YARD)), inner_r=0.8, rings=7, seed=SEED)
    yard.finish(ctx.collection)

    post = mesh.Batch("Signpost_0")
    post.beam(timber, (10.0, 6.0, h(10.0, 6.0)), (10.0, 6.0, h(10.0, 6.0) + 2.3), 0.06, sides=8)
    post.box(timber, (10.0, 6.0, h(10.0, 6.0) + 2.0), (1.6, 0.06, 0.45))
    root = post.finish(ctx.collection)[0]
    sign = architecture.text_mesh("SignText_0", ctx.collection, "OLD MILL INN", (10.0, 5.95, h(10.0, 6.0) + 1.85), 0.22,
                                  materials.material("SignPaint", "F2E6C8", roughness=0.6))
    sign.parent = root                             # the text rides the post: one placement row
    sign.matrix_parent_inverse = place.world_matrix(root).inverted()

    coats = [materials.material("CoatRed", "7A2E2E", roughness=0.85), materials.material("CoatBlue", "2E4A7A", roughness=0.85),
             materials.material("CoatGreen", "4A6A3A", roughness=0.85), materials.material("Skin", "D9A880", roughness=0.6)]
    folk = mesh.Batch("Villagers_0")
    spots = [(2.5, 4.0), (6.0, 3.0), (7.5, 9.5), (-1.0, 9.0)]
    figure.crowd(folk, coats, [(x, y, h(x, y)) for x, y in spots], rng)
    folk.finish(ctx.collection)

    lantern = ctx.assets.get("lantern_post")
    if lantern is not None:
        scatter.scatter(ctx.collection, lantern, "LanternPost", [(-1.0, 4.5), (9.0, 3.5), (12.5, 10.5)], height_at=h, rng=rng, rot=0.0)
    crate = ctx.assets.get("crate")
    if crate is not None:
        for i, (x, y, yaw) in enumerate(((3.6, 14.5, 0.3), (4.5, 15.2, 1.2), (13.2, 13.5, 0.0))):
            scatter.instance(ctx.collection, crate, place.unique_name("Crate", i), x, y, height_at=h, rot_z=yaw)
    return ctx.collection
