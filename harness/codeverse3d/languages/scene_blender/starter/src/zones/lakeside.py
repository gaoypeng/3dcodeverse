"""src/zones/lakeside.py — zone "Lakeside": the lake in the env's basin, a wooden footbridge over
its edge, shore rocks, reeds and a willow.

CONTRACT: build(ctx) -> bpy.types.Collection — every placed thing is ONE root object seated on
ctx.height_at; the water is a water-named surface (the census files it as a backdrop and a foot
in it as on water).
"""
from lib import architecture, materials, mesh, terrain, vegetation, water

SEED = 21
LAKE = (-24.0, 2.0)   # == env.BASIN's centre


def build(ctx):
    rng = ctx.rng(SEED)
    h = ctx.height_at
    level = float(ctx.env.get("water_level", -0.3))

    lake = water.water_surface("LakeWater", ctx.collection, size=(34.0, 34.0), level=level,
                               material=materials.water("LakeWaterMat", color="1A3F4A", ctx=ctx),
                               height_fn=lambda x, y: h(x + LAKE[0], y + LAKE[1]))
    lake.location = (LAKE[0], LAKE[1], 0.0)

    rock = materials.mineral_surface("ShoreRock", "6F6A60", stone=True)
    rocks = mesh.Batch("ShoreRocks_0")
    terrain.rock_field(rocks, rock, (LAKE[0] + 13.0, LAKE[1] - 7.0), 4.5, 14, h, rng)
    terrain.boulder(rocks, rock, (LAKE[0] + 11.0, LAKE[1] + 10.0, h(LAKE[0] + 11.0, LAKE[1] + 10.0)), 1.6, seed=3)
    rocks.finish(ctx.collection)

    wood = materials.woodgrain(materials.material("BridgeWood", "5A4330", roughness=0.8))
    stone = materials.ashlar("BridgeStone", "9A9080")
    bridge = mesh.Batch("Footbridge_0")
    start, end = (LAKE[0] + 12.7, LAKE[1] - 12.7), (LAKE[0] + 8.5, LAKE[1] - 8.5)   # bank top → the shallows
    architecture.arched_footbridge(bridge, wood, stone, start=(*start, h(*start)), end=(*end, h(*end)), rise=1.1, height_at=h)
    bridge.finish(ctx.collection)

    reed_bed = terrain.ground("ReedSurface", ctx.collection, size=(9.0, 6.0), segments=6, height_at=h, material=None,
                              center=(LAKE[0] + 13.5, LAKE[1] + 5.0))
    reed_bed.hide_render = True                    # the distribution domain only: the cloud draws
    reed_bed.hide_viewport = True
    vegetation.grass_patch("Reeds_0", ctx.collection, reed_bed, density=5.0, seed=SEED, height=1.2, color="6B7A3A")

    bark = materials.material("WillowBark", "4A3A2A", roughness=0.9, noise_scale=6.0)
    leaves = materials.foliage("WillowLeaf", "6A8A3A", variants=2)
    willow = mesh.Batch("Willow_0")
    wx, wy = LAKE[0] + 16.5, LAKE[1] + 1.0
    vegetation.tree(willow, (wx, wy, h(wx, wy)), 7.5, bark, leaves[0], seed=SEED, kind="broadleaf", max_leaves=1800)
    willow.finish(ctx.collection)
    return ctx.collection
