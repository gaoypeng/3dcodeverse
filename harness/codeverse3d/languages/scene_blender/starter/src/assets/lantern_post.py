"""src/assets/lantern_post.py — asset "LanternPost": a 2.4 m timber post with a bracket arm and a
glowing paper lantern, plus the light it throws.

CONTRACT: build_lantern_post(ctx, variant=0) -> bpy.types.Collection — built ONCE at the origin
  with its base on z = 0; zones place collection instances (ctx.assets['lantern_post']).  A lamp
  inside the collection is instanced with it, so every post lights its own patch.
"""
from lib import architecture, lighting, materials
from lib.mesh import Batch


def build_lantern_post(ctx, variant=0):
    wood = materials.woodgrain(materials.material("PostWood", "3A2A1A", roughness=0.85))
    glow = materials.emissive("LanternGlow", "FFB270", 5.0)
    b = Batch("LanternPost")
    b.beam(wood, (0.0, 0.0, 0.0), (0.0, 0.0, 2.4), 0.06, sides=8)
    b.beam(wood, (0.0, 0.0, 2.3), (0.45, 0.0, 2.3), 0.035, sides=6)
    b.beam(wood, (0.0, 0.0, 1.95), (0.42, 0.0, 2.28), 0.025, sides=6)
    architecture.lantern(b, glow, wood, (0.45, 0.0, 1.85), radius=0.17)
    b.finish(ctx.collection, parent=False)
    lighting.practical(ctx.collection, "LanternPostLight", (0.45, 0.0, 1.72), color="FFB270", energy=25.0, size=0.25)
    return ctx.collection
