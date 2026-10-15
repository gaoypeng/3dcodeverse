"""src/assets/crate.py — asset "Crate": a slatted wooden crate, 0.9 m, corner posts and boards.

CONTRACT: build_crate(ctx, variant=0) -> bpy.types.Collection — link every part into ctx.collection
  (named 'Crate'), built ONCE at the origin with its base on z = 0, front facing -Y.  Zones place
  collection instances of it (ctx.assets['crate']); never link it anywhere else.
"""
from lib.materials import material, woodgrain
from lib.mesh import Batch

SIZE = 0.9


def build_crate(ctx, variant=0):
    wood = woodgrain(material("CrateWood", "8A6A3F", roughness=0.8), dark="3A2A18", light="9A7A4A")
    s = SIZE * (1.0 + 0.08 * (variant % 3))
    post, slat = 0.06, 0.05
    rim = s * 0.87 + 0.14          # the top slat's top: the posts reach it, so a stacked crate rests on all four corners
    b = Batch("Crate")
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.box(wood, (sx * (s / 2 - post / 2), sy * (s / 2 - post / 2), rim / 2), (post, post, rim))
    for k in range(4):                              # three slats per side, a gap between them
        z = s * (0.12 + 0.25 * k) + 0.07
        for sy in (-1, 1):
            b.box(wood, (0.0, sy * (s / 2 - slat / 2), z), (s - 2 * post, slat, 0.14))
        for sx in (-1, 1):
            b.box(wood, (sx * (s / 2 - slat / 2), 0.0, z), (slat, s - 2 * post, 0.14))
    b.box(wood, (0.0, 0.0, 0.025), (s - 0.02, s - 0.02, 0.05))      # the floor board
    b.finish(ctx.collection, parent=False)
    return ctx.collection
