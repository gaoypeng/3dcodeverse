"""SteamWand — articulating milk frothing steam pipe and nozzle.

Curved stainless-steel tube mounted via a ball joint on the right face, ending in a conical frothing tip and rubber heat guard.
Material: polished stainless steel with silicone finger grip.
Plan bbox: center (0.110, -0.120, 0.160) extents (0.050, 0.080, 0.140)
  x in [0.085, 0.135], y in [-0.160, -0.080], z in [0.090, 0.230]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

def make_material(name, rgb, roughness=0.1, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_steam_wand():
    """Build steam wand: ball joint mount, upper pipe, curved elbow, lower pipe, silicone grip, and steam tip."""
    bm = bmesh.new()
    
    r_pipe = 0.004  # 8mm diameter pipe
    
    # 1. Solid mounting socket / bracket extending directly into the chassis under-panel (z >= 0.238)
    # The overhang bottom is at z=0.238, front face at y=-0.100.
    # Mounting flange firmly welded into the underside of the chassis overhang:
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.010, radius2=0.010, depth=0.024)
    bmesh.ops.translate(bm, vec=(0.105, -0.080, 0.240), verts=res["verts"])

    # Ball joint knuckle & socket base
    res = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=12, radius=0.009)
    bmesh.ops.translate(bm, vec=(0.105, -0.080, 0.228), verts=res["verts"])
    
    # Horizontal collar connecting back to splash wall / chassis structure
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.007, radius2=0.007, depth=0.020)
    rot_x90 = Matrix.Rotation(math.radians(90), 4, 'X')
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.105, -0.075, 0.228), verts=res["verts"])
    
    # 2. Upper angled section (reaches forward-outward): from (0.105, -0.080, 0.228) to (0.118, -0.115, 0.175)
    p1 = Vector((0.105, -0.080, 0.228))
    p2 = Vector((0.118, -0.115, 0.175))
    v12 = p2 - p1
    len12 = v12.length
    
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_pipe, radius2=r_pipe, depth=len12)
    # Align cylinder Z with vector v12
    q = Vector((0, 0, 1)).rotation_difference(v12)
    bmesh.ops.transform(bm, matrix=q.to_matrix().to_4x4(), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(p1 + p2)/2.0, verts=res["verts"])
    
    # 3. Rubber heat-guard sleeve on upper-mid section
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_pipe + 0.003, radius2=r_pipe + 0.003, depth=0.025)
    bmesh.ops.transform(bm, matrix=q.to_matrix().to_4x4(), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(p1 + p2)/2.0, verts=res["verts"])
    
    # 4. Elbow joint sphere
    res = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=12, radius=r_pipe * 1.3)
    bmesh.ops.translate(bm, vec=p2, verts=res["verts"])
    
    # 5. Lower pipe section (points downward and slightly forward towards drip tray):
    # from (0.118, -0.115, 0.175) to (0.105, -0.145, 0.095)
    p3 = Vector((0.105, -0.145, 0.095))
    v23 = p3 - p2
    len23 = v23.length
    
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_pipe, radius2=r_pipe, depth=len23)
    q2 = Vector((0, 0, 1)).rotation_difference(v23)
    bmesh.ops.transform(bm, matrix=q2.to_matrix().to_4x4(), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(p2 + p3)/2.0, verts=res["verts"])
    
    # 6. Frothing nozzle tip (conical chrome tip at p3)
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.003, radius2=0.006, depth=0.015)
    bmesh.ops.transform(bm, matrix=q2.to_matrix().to_4x4(), verts=res["verts"])
    bmesh.ops.translate(bm, vec=p3, verts=res["verts"])

    me = bpy.data.meshes.new("SteamWand")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SteamWand", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("SteamWandChrome", (0.95, 0.95, 0.95), roughness=0.1, metallic=1.0)
    obj.data.materials.append(mat)
    
    return obj
