"""SideVents — Motor heat dissipation louvers (part module; imported by src/model.py).

Slotted cooling grill with 5 horizontal parallel louver ribs cut into each lateral side of the rear motor housing.
Material: Dark charcoal molded grill slats.  Instances: 2 (mirror_x).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.037, 0.035, 0.190) extents (0.004, 0.045, 0.026)
# x in [0.035, 0.039]
# y in [0.0125, 0.0575] (length = 0.045)
# z in [0.177, 0.203] (height = 0.026)
# SideVents_0 at +x, SideVents_1 at -x

def make_material(name, rgb, roughness=0.6, metallic=0.1):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_side_vents():
    mat_grill = make_material("GrillDarkCharcoal", (0.06, 0.06, 0.07), roughness=0.7, metallic=0.0)

    objs = []
    # 5 horizontal parallel louvers per side
    n_slats = 5
    slat_h = 0.0030
    slat_len = 0.045
    slat_thick = 0.004  # thickness along X

    z_start = 0.177 + slat_h / 2
    z_pitch = (0.026 - slat_h) / (n_slats - 1)

    for side_idx, sign in enumerate([1, -1]):
        bm = bmesh.new()
        xc = sign * 0.037
        
        # Perimeter grill frame
        for s in range(n_slats):
            zc = z_start + s * z_pitch
            bm_slat = bmesh.new()
            bmesh.ops.create_cube(bm_slat, size=1.0)
            # Scale
            for v in bm_slat.verts:
                v.co.x = xc + v.co.x * slat_thick
                v.co.y = 0.035 + v.co.y * (slat_len - 0.004)
                v.co.z = zc + v.co.z * slat_h
            
            offset = len(bm.verts)
            for v in bm_slat.verts:
                bm.verts.new(v.co)
            bm.verts.ensure_lookup_table()
            for f in bm_slat.faces:
                bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
            bm_slat.free()

        # Side frame borders (front and back vertical trim)
        for y_pos in [0.035 - (slat_len / 2 - 0.002), 0.035 + (slat_len / 2 - 0.002)]:
            bm_post = bmesh.new()
            bmesh.ops.create_cube(bm_post, size=1.0)
            for v in bm_post.verts:
                v.co.x = xc + v.co.x * slat_thick
                v.co.y = y_pos + v.co.y * 0.004
                v.co.z = 0.190 + v.co.z * 0.026
            offset = len(bm.verts)
            for v in bm_post.verts:
                bm.verts.new(v.co)
            bm.verts.ensure_lookup_table()
            for f in bm_post.faces:
                bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
            bm_post.free()

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

        name = f"SideVents_{side_idx}"
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        me.update()

        obj = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat_grill)

        bev = obj.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.0004
        bev.segments = 2
        bev.limit_method = "ANGLE"

        objs.append(obj)

    return objs
