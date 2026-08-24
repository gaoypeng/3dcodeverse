"""SideWindowFrame — Window casing and internal mullion crossbars.

Square timber window frame mounted flush on the right (+X) wall with a central cross (4-pane mullion grid), 40 mm frame thickness.
Material: painted white/cream timber window frame.
Bbox: center (0.905, 0.150, 1.200) extents (0.050, 0.650, 0.650)
  x in [0.880, 0.930], y in [-0.175, 0.475], z in [0.875, 1.525]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_side_window_frame() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Outer frame: y in [-0.175, 0.475] (w = 0.650), z in [0.875, 1.525] (h = 0.650)
    # x in [0.880, 0.930] (d = 0.050, center = 0.905)
    fw = 0.050 # outer casing width in Y/Z
    fd = 0.050 # thickness along X
    xc = 0.905
    
    # 1. Outer Frame
    # Bottom sill
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fd, 0.650, fw), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(xc, 0.150, 0.875 + fw/2), verts=bm.verts[-8:])
    
    # Top head
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fd, 0.650, fw), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(xc, 0.150, 1.525 - fw/2), verts=bm.verts[-8:])
    
    # Left jamb (front side, y = -0.175)
    inner_h = 0.650 - 2 * fw
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fd, fw, inner_h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(xc, -0.175 + fw/2, 1.200), verts=bm.verts[-8:])
    
    # Right jamb (back side, y = 0.475)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fd, fw, inner_h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(xc, 0.475 - fw/2, 1.200), verts=bm.verts[-8:])
    
    # 2. Central 4-pane cross grid (mullion and transom)
    mw = 0.025 # mullion width
    inner_w = 0.650 - 2 * fw
    
    # Vertical mullion at y = 0.150
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fd - 0.010, mw, inner_h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(xc, 0.150, 1.200), verts=bm.verts[-8:])
    
    # Horizontal transom at z = 1.200
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fd - 0.010, inner_w, mw), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(xc, 0.150, 1.200), verts=bm.verts[-8:])

    bm.normal_update()
    
    me = bpy.data.meshes.new("SideWindowFrame")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SideWindowFrame", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("PaintedWindowWhite", (0.92, 0.90, 0.85), roughness=0.45)
    obj.data.materials.append(mat)
    return obj
