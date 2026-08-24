"""DripGrate — removable slotted drainage plate on top of the drip tray.

Perforated metal plate with an array of oval drainage slots and a front center finger notch.
Material: polished stainless steel with punched slots.
Plan bbox: center (0.000, -0.140, 0.074) extents (0.246, 0.116, 0.005)
  x in [-0.123, 0.123], y in [-0.198, -0.082], z in [0.071, 0.076]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.15, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_drip_grate():
    """Build the drip grate slotted surface with ribs and drainage cutouts."""
    bm = bmesh.new()
    
    # Outer frame of the grate
    # Base thin sheet
    # We can create a base plate and longitudinal / transversal slotted ribs
    # Frame perimeter
    t = 0.005 # thickness
    w_frame = 0.008
    
    # Let's create the perimeter bars and a grid of drainage slats
    # X span: -0.123 to 0.123 (total 0.246)
    # Y span: -0.198 to -0.082 (total 0.116)
    # Z span: 0.071 to 0.076 (center 0.0735, height 0.005)
    
    # Base solid rim/sheet with slots represented by slat geometry:
    # 1. Front border bar
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.246, w_frame, t), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(0.000, -0.198 + w_frame/2.0, 0.0735), verts=bm.verts[v_start:])
    
    # 2. Back border bar
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.246, w_frame, t), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(0.000, -0.082 - w_frame/2.0, 0.0735), verts=bm.verts[v_start:])
    
    # 3. Left border bar
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(w_frame, 0.116 - 2*w_frame, t), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(-0.123 + w_frame/2.0, -0.140, 0.0735), verts=bm.verts[v_start:])
    
    # 4. Right border bar
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(w_frame, 0.116 - 2*w_frame, t), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(0.123 - w_frame/2.0, -0.140, 0.0735), verts=bm.verts[v_start:])
    
    # 5. Internal drainage slats (array of slats along X)
    n_slats = 14
    x_inner_start = -0.123 + w_frame + 0.008
    x_inner_end = 0.123 - w_frame - 0.008
    slat_w = 0.006
    slat_len = 0.116 - 2*w_frame - 0.004
    
    step = (x_inner_end - x_inner_start) / (n_slats - 1)
    for i in range(n_slats):
        sx = x_inner_start + i * step
        v_start = len(bm.verts)
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(slat_w, slat_len, t), verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(sx, -0.140, 0.0735), verts=bm.verts[v_start:])
        
    # 6. Central spine bar for rigidity
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.246 - 2*w_frame, 0.008, t), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(0.000, -0.140, 0.0735), verts=bm.verts[v_start:])

    me = bpy.data.meshes.new("DripGrate")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("DripGrate", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("PolishedGrate", (0.90, 0.90, 0.92), roughness=0.15, metallic=1.0)
    obj.data.materials.append(mat)
    
    return obj
