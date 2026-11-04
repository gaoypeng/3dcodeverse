"""src/assets/crate.py — asset "Crate": a slatted wooden crate, 0.9 m, corner posts and boards.

CONTRACT: build_crate(ctx, variant=0) -> bpy.types.Collection — link every part into ctx.collection
  (named 'Crate'), built ONCE at the origin with its base on z = 0, front facing -Y.  Zones place
  collection instances of it (ctx.assets['crate']); never link it anywhere else.  An asset module is
  self-contained: it is built and checked without `env`.
"""
import bmesh
import bpy

SIZE = 0.9


def _box(bm, center, size):
    geom = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=size, verts=geom)
    bmesh.ops.translate(bm, vec=center, verts=geom)


def build_crate(ctx, variant=0):
    wood = bpy.data.materials.new("CrateWood")
    bsdf = wood.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.54, 0.42, 0.25, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.8
    s = SIZE * (1.0 + 0.08 * (variant % 3))
    post, slat = 0.06, 0.05
    rim = s * 0.87 + 0.14          # the top slat's top: the posts reach it, so a stacked crate rests on all four corners
    bm = bmesh.new()
    for sx in (-1, 1):
        for sy in (-1, 1):
            _box(bm, (sx * (s / 2 - post / 2), sy * (s / 2 - post / 2), rim / 2), (post, post, rim))
    for k in range(4):                              # three slats per side, a gap between them
        z = s * (0.12 + 0.25 * k) + 0.07
        for sy in (-1, 1):
            _box(bm, (0.0, sy * (s / 2 - slat / 2), z), (s - 2 * post, slat, 0.14))
        for sx in (-1, 1):
            _box(bm, (sx * (s / 2 - slat / 2), 0.0, z), (slat, s - 2 * post, 0.14))
    _box(bm, (0.0, 0.0, 0.025), (s - 0.02, s - 0.02, 0.05))      # the floor board
    me = bpy.data.meshes.new("Crate")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(wood)
    ctx.collection.objects.link(bpy.data.objects.new("Crate", me))
    return ctx.collection
