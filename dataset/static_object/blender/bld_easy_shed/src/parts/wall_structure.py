"""WallStructure — Main rectangular enclosing walls.

Box wall assembly with horizontal tongue-and-groove/lap siding cladding, vertical corner trim battens (40x40 mm),
and openings cut out for the front door and side window.
Material: natural cedar horizontal weatherboard.
Bbox: center (0.000, 0.000, 0.975) extents (1.800, 2.000, 1.750)
  x in [-0.900, 0.900], y in [-1.000, 1.000], z in [0.100, 1.850]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_wall_structure() -> bpy.types.Object:
    bm = bmesh.new()
    
    wall_thick = 0.06
    # Outer bounds: x in [-0.90, 0.90], y in [-1.00, 1.00], z in [0.10, 1.85]
    # Openings:
    # Front door opening (-Y): x in [-0.490, 0.290], z in [0.100, 1.750]
    # Side window opening (+X): y in [-0.175, 0.475], z in [0.875, 1.525]
    
    # 1. Back wall (Y = +1.00): full width x in [-0.90, 0.90], y in [1.00 - wall_thick, 1.00], z in [0.10, 1.85]
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.800, wall_thick, 1.750), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.0, 1.000 - wall_thick/2, 0.975), verts=bm.verts[-8:])
    
    # 2. Left wall (X = -0.90): full wall x in [-0.90, -0.90 + wall_thick], y in [-1.00, 1.00 - wall_thick], z in [0.10, 1.85]
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(wall_thick, 2.000 - wall_thick, 1.750), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.900 + wall_thick/2, -wall_thick/2, 0.975), verts=bm.verts[-8:])
    
    # 3. Right wall (X = +0.90) with window opening at y in [-0.175, 0.475], z in [0.875, 1.525]
    # Left segment of right wall (y in [0.475, 1.00 - wall_thick]): len = (1.00 - wall_thick) - 0.475 = 0.94 - 0.475 = 0.465
    y_r_back_len = (1.000 - wall_thick) - 0.475
    y_r_back_mid = 0.475 + y_r_back_len / 2
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(wall_thick, y_r_back_len, 1.750), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.900 - wall_thick/2, y_r_back_mid, 0.975), verts=bm.verts[-8:])
    
    # Front segment of right wall (y in [-1.00, -0.175]): len = 0.825
    y_r_front_len = -0.175 - (-1.000)
    y_r_front_mid = -1.000 + y_r_front_len / 2
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(wall_thick, y_r_front_len, 1.750), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.900 - wall_thick/2, y_r_front_mid, 0.975), verts=bm.verts[-8:])
    
    # Below window (y in [-0.175, 0.475], z in [0.100, 0.875]): h = 0.775
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(wall_thick, 0.650, 0.775), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.900 - wall_thick/2, 0.150, 0.100 + 0.775/2), verts=bm.verts[-8:])
    
    # Above window (y in [-0.175, 0.475], z in [1.525, 1.850]): h = 0.325
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(wall_thick, 0.650, 0.325), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.900 - wall_thick/2, 0.150, 1.525 + 0.325/2), verts=bm.verts[-8:])
    
    # 4. Front wall (Y = -1.00) with door opening at x in [-0.490, 0.290], z in [0.100, 1.750]
    # Left segment of front wall (x in [-0.900 + wall_thick, -0.490]): len = -0.490 - (-0.840) = 0.350
    x_f_left_len = -0.490 - (-0.900 + wall_thick)
    x_f_left_mid = (-0.900 + wall_thick) + x_f_left_len / 2
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(x_f_left_len, wall_thick, 1.750), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(x_f_left_mid, -1.000 + wall_thick/2, 0.975), verts=bm.verts[-8:])
    
    # Right segment of front wall (x in [0.290, 0.900 - wall_thick]): len = 0.840 - 0.290 = 0.550
    x_f_right_len = (0.900 - wall_thick) - 0.290
    x_f_right_mid = 0.290 + x_f_right_len / 2
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(x_f_right_len, wall_thick, 1.750), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(x_f_right_mid, -1.000 + wall_thick/2, 0.975), verts=bm.verts[-8:])
    
    # Above door lintel (x in [-0.490, 0.290], z in [1.750, 1.850]): h = 0.100
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.780, wall_thick, 0.100), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.100, -1.000 + wall_thick/2, 1.800), verts=bm.verts[-8:])
    
    # Add horizontal siding battens/grooves or corner trim posts
    # Corner posts (4 corners): 0.05 x 0.05 x 1.75
    corner_size = 0.05
    for cx, cy in [(-0.900 + corner_size/2, -1.000 + corner_size/2),
                   (0.900 - corner_size/2, -1.000 + corner_size/2),
                   (-0.900 + corner_size/2, 1.000 - corner_size/2),
                   (0.900 - corner_size/2, 1.000 - corner_size/2)]:
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(corner_size, corner_size, 1.750), verts=bm.verts[-8:])
        bmesh.ops.translate(bm, vec=(cx, cy, 0.975), verts=bm.verts[-8:])
        
    # Horizontal siding detail ribs (lap siding lines on walls)
    # Left wall horizontal slats:
    n_slats = 16
    for i in range(n_slats):
        sz = 0.100 + (i + 0.5) * (1.750 / n_slats)
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.012, 1.960, 0.090), verts=bm.verts[-8:])
        bmesh.ops.translate(bm, vec=(-0.900 + 0.006, 0.0, sz), verts=bm.verts[-8:])
        
    # Back wall horizontal slats:
    for i in range(n_slats):
        sz = 0.100 + (i + 0.5) * (1.750 / n_slats)
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(1.760, 0.012, 0.090), verts=bm.verts[-8:])
        bmesh.ops.translate(bm, vec=(0.0, 1.000 - 0.006, sz), verts=bm.verts[-8:])

    me = bpy.data.meshes.new("WallStructure")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("WallStructure", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("CedarWeatherboard", (0.72, 0.48, 0.28), roughness=0.65)
    obj.data.materials.append(mat)
    return obj
