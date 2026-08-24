"""DrawerPull — ornate brass bail pull handles.

Antiqued cast brass drawer pulls comprising a pair of rosettes and a curved drop-loop bail handle mounted centrally on each drawer front.
Material: antiqued polished brass, satin gold metallic. Instances: 6 (mirror_x).
"""
import bpy
import bmesh
from mathutils import Matrix

# Plan: bbox center (0.480, -0.355, 0.410) extents (0.120, 0.025, 0.045)
# DrawerFront front face is at y = -0.335 - 0.011 = -0.346.
# DrawerPull extends from y = -0.3425 to -0.3675 (extents 0.025, center -0.355).
# Rosette back face sits at local y = 0.009 (world y = -0.346), precisely touching the drawer front.
PULL_POSITIONS = [
    # Right pedestal drawers
    ( 0.480, -0.355, 0.605),
    ( 0.480, -0.355, 0.380),
    ( 0.480, -0.355, 0.155),
    # Left pedestal drawers
    (-0.480, -0.355, 0.605),
    (-0.480, -0.355, 0.380),
    (-0.480, -0.355, 0.155),
]

def make_material(name, rgb, roughness=0.25, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_drawer_pull():
    mat = make_material("AntiquedBrassPull", (0.85, 0.68, 0.22), roughness=0.28, metallic=0.95)
    objs = []
    
    rot_x_90 = Matrix.Rotation(1.570796, 3, 'X')
    rot_y_90 = Matrix.Rotation(1.570796, 3, 'Y')
    
    for i, pos in enumerate(PULL_POSITIONS):
        bm = bmesh.new()
        
        # Rosettes: span from -0.060 to +0.060 in X (total 0.120m)
        # Rosette radius = 0.015, center at x = +/-0.045
        # Rosette depth = 0.005, centered at local y = 0.0065 -> back face at local y = +0.009 (world -0.346)
        rosette_r = 0.015
        rosette_t = 0.005
        # Left rosette
        bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=rosette_r, radius2=rosette_r, depth=rosette_t)
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=rot_x_90, verts=bm.verts)
        bmesh.ops.translate(bm, vec=(-0.045, 0.0065, 0.0075), verts=bm.verts)
        
        # Right rosette
        v_start = len(bm.verts)
        bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=rosette_r, radius2=rosette_r, depth=rosette_t)
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=rot_x_90, verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(0.045, 0.0065, 0.0075), verts=bm.verts[v_start:])
        
        # Bail handle (drop loop bar) at local Y = -0.007
        v_start = len(bm.verts)
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.005, radius2=0.005, depth=0.090)
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=rot_y_90, verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(0, -0.007, -0.0175), verts=bm.verts[v_start:])
        
        # Left post
        v_start = len(bm.verts)
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.004, radius2=0.004, depth=0.013)
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=rot_x_90, verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(-0.045, 0.000, -0.005), verts=bm.verts[v_start:])
        
        # Right post
        v_start = len(bm.verts)
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.004, radius2=0.004, depth=0.013)
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=rot_x_90, verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(0.045, 0.000, -0.005), verts=bm.verts[v_start:])
        
        me = bpy.data.meshes.new(f"DrawerPull_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"DrawerPull_{i}", me)
        obj.location = pos
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        
        objs.append(obj)
        
    return objs
