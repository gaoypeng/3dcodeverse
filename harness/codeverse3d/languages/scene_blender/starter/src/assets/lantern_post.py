"""src/assets/lantern_post.py — asset "LanternPost": a 2.4 m timber post with a bracket arm and a
glowing paper lantern, plus the light it throws.

CONTRACT: build_lantern_post(ctx, variant=0) -> bpy.types.Collection — built ONCE at the origin
  with its base on z = 0; zones place collection instances (ctx.assets['lantern_post']).  A lamp
  inside the collection is instanced with it, so every post lights its own patch.
"""
import bmesh
import bpy
from mathutils import Matrix


def _principled(name, rgb, roughness=0.8, emission=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def _beam(bm, start, end, radius, slot, sides=8):
    """A cylinder from `start` to `end` (vertical, or horizontal along X), its faces on material `slot`."""
    sx, sy, sz = start
    ex, ey, ez = end
    length = ((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2) ** 0.5
    geom = bmesh.ops.create_cone(bm, segments=sides, radius1=radius, radius2=radius, depth=length, cap_ends=True)["verts"]
    faces = {f for v in geom for f in v.link_faces}
    if abs(ez - sz) < 1e-6:                                   # a horizontal arm: lie the cylinder along X
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(1.5707963, 3, "Y"), verts=geom)
    bmesh.ops.translate(bm, vec=((sx + ex) / 2, (sy + ey) / 2, (sz + ez) / 2), verts=geom)
    for f in faces:
        f.material_index = slot


def build_lantern_post(ctx, variant=0):
    wood = _principled("PostWood", (0.23, 0.16, 0.10), roughness=0.85)
    glow = _principled("LanternGlow", (1.0, 0.70, 0.44), roughness=0.6, emission=5.0)
    bm = bmesh.new()
    _beam(bm, (0.0, 0.0, 0.0), (0.0, 0.0, 2.4), 0.06, 0)               # the post
    _beam(bm, (0.0, 0.0, 2.3), (0.45, 0.0, 2.3), 0.035, 0, sides=6)     # the bracket arm
    lantern = bmesh.ops.create_cube(bm, size=1.0)["verts"]              # the paper lantern, hung from the arm's end
    bmesh.ops.scale(bm, vec=(0.26, 0.26, 0.34), verts=lantern)
    bmesh.ops.translate(bm, vec=(0.45, 0.0, 2.05), verts=lantern)
    for f in {f for v in lantern for f in v.link_faces}:
        f.material_index = 1
    me = bpy.data.meshes.new("LanternPost")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(wood)
    me.materials.append(glow)
    ctx.collection.objects.link(bpy.data.objects.new("LanternPost", me))
    light = bpy.data.lights.new("LanternPostLight", "POINT")             # the glow is the look; the lamp is the light
    light.energy, light.color, light.shadow_soft_size = 25.0, (1.0, 0.70, 0.44), 0.25
    lamp = bpy.data.objects.new("LanternPostLight", light)
    lamp.location = (0.45, 0.0, 1.85)
    ctx.collection.objects.link(lamp)
    return ctx.collection
