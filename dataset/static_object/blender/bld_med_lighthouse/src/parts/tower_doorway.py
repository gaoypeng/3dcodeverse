"""TowerDoorway — ground-level arched access entrance.
Projecting heavy timber arched entrance door with stone arch surround facing front (-Y),
width 0.90 m, height 1.70 m, projection 0.40 m.
BBox: center (0.000, -1.600, 2.900) extents (0.900, 0.400, 1.700)
x in [-0.45, 0.45], y in [-1.80, -1.40], z in [2.05, 3.75].
"""
import math
import bpy
import bmesh
from mathutils import Vector

TOWER_DOORWAY_CENTER = (0.000, -1.600, 2.900)
TOWER_DOORWAY_EXTENTS = (0.900, 0.400, 1.700)

def make_material(name, rgb, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_tower_doorway() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Outer bounds:
    # x in [-0.45, 0.45], y in [-1.80, -1.40], z in [2.05, 3.75]
    # Doorway: stone surround arch + solid wooden door panel + threshold sill.
    
    n_arch = 16
    
    # Outer arch profile in XZ from z = 2.202 to 3.75:
    pts_outer = []
    pts_outer.append((0.45, 2.202))
    pts_outer.append((0.45, 3.30))
    for i in range(1, n_arch):
        ang = math.pi * i / n_arch
        pts_outer.append((0.45 * math.cos(ang), 3.30 + 0.45 * math.sin(ang)))
    pts_outer.append((-0.45, 3.30))
    pts_outer.append((-0.45, 2.202))
    
    # Inner opening profile in XZ (frame width 0.08m):
    pts_inner = []
    pts_inner.append((0.37, 2.202))
    pts_inner.append((0.37, 3.22))
    for i in range(1, n_arch):
        ang = math.pi * i / n_arch
        pts_inner.append((0.37 * math.cos(ang), 3.22 + 0.37 * math.sin(ang)))
    pts_inner.append((-0.37, 3.22))
    pts_inner.append((-0.37, 2.202))
    
    N = len(pts_outer)
    
    # 1. Main upper doorway frame (z in [2.202, 3.75]):
    # In this height range, TowerPlinth has ended (z_max = 2.200).
    # Front is at y = -1.80, back is at y = -1.40 (embedding into TowerShaft).
    v_front_out = [bm.verts.new((x, -1.80, z)) for x, z in pts_outer]
    v_back_out = [bm.verts.new((x, -1.40, z)) for x, z in pts_outer]
    v_front_in = [bm.verts.new((x, -1.74, z)) for x, z in pts_inner]
    v_back_in = [bm.verts.new((x, -1.40, z)) for x, z in pts_inner]
    
    # Outer side walls (left, right, curved top)
    for i in range(N - 1):
        bm.faces.new([v_front_out[i], v_back_out[i], v_back_out[i+1], v_front_out[i+1]])
        
    # Front face of stone frame
    for i in range(N - 1):
        bm.faces.new([v_front_out[i], v_front_out[i+1], v_front_in[i+1], v_front_in[i]])
    
    # Inner jamb sides (from front_in to back_in)
    for i in range(N - 1):
        bm.faces.new([v_front_in[i+1], v_back_in[i+1], v_back_in[i], v_front_in[i]])
        
    # Back face of stone frame (between v_back_out and v_back_in)
    for i in range(N - 1):
        bm.faces.new([v_back_out[i+1], v_back_out[i], v_back_in[i], v_back_in[i+1]])
    
    # 2. Wooden Door Panel in upper frame (z in [2.202, 3.59]):
    v_door_f = [bm.verts.new((x, -1.68, z)) for x, z in pts_inner]
    v_door_b = [bm.verts.new((x, -1.64, z)) for x, z in pts_inner]
    
    # Door front faces:
    f_rect = bm.faces.new([v_door_f[0], v_door_f[1], v_door_f[N-2], v_door_f[N-1]])
    f_rect.material_index = 1
    center_arch_f = bm.verts.new((0.0, -1.68, 3.22))
    for i in range(1, N - 2):
        f = bm.faces.new([v_door_f[i], v_door_f[i+1], center_arch_f])
        f.material_index = 1
        
    # Door back faces:
    b_rect = bm.faces.new([v_door_b[N-1], v_door_b[N-2], v_door_b[1], v_door_b[0]])
    b_rect.material_index = 1
    center_arch_b = bm.verts.new((0.0, -1.64, 3.22))
    for i in range(1, N - 2):
        f = bm.faces.new([v_door_b[i+1], v_door_b[i], center_arch_b])
        f.material_index = 1
        
    # Door rim:
    for i in range(N - 1):
        f = bm.faces.new([v_door_f[i], v_door_b[i], v_door_b[i+1], v_door_f[i+1]])
        f.material_index = 1
    f_bot = bm.faces.new([v_door_f[-1], v_door_b[-1], v_door_b[0], v_door_f[0]])
    f_bot.material_index = 1

    # 3. Threshold Sill / Base Step (z in [2.05, 2.202]):
    # Stays strictly at y in [-1.80, -1.701] so it rests in front of the plinth without entering it.
    # Vertices of the sill box:
    s_f_bl = bm.verts.new((-0.45, -1.80, 2.05))
    s_f_br = bm.verts.new(( 0.45, -1.80, 2.05))
    s_b_br = bm.verts.new(( 0.45, -1.701, 2.05))
    s_b_bl = bm.verts.new((-0.45, -1.701, 2.05))
    
    s_f_tl = v_front_out[-1] # (-0.45, -1.80, 2.202)
    s_f_tr = v_front_out[0]  # ( 0.45, -1.80, 2.202)
    s_b_tr = bm.verts.new(( 0.45, -1.701, 2.202))
    s_b_tl = bm.verts.new((-0.45, -1.701, 2.202))
    
    # Bottom face of sill
    bm.faces.new([s_f_bl, s_b_bl, s_b_br, s_f_br])
    # Front face of sill
    bm.faces.new([s_f_bl, s_f_br, s_f_tr, s_f_tl])
    # Right side of sill
    bm.faces.new([s_f_br, s_b_br, s_b_tr, s_f_tr])
    # Left side of sill
    bm.faces.new([s_b_bl, s_f_bl, s_f_tl, s_b_tl])
    # Back face of sill (touching / facing plinth)
    bm.faces.new([s_b_br, s_b_bl, s_b_tl, s_b_tr])
    
    # Top face of sill between y = -1.80 and y = -1.701
    bm.faces.new([s_f_tl, s_f_tr, s_b_tr, s_b_tl])
    
    # Bottom caps for frame/jambs at z = 2.202 (between y = -1.701 and y = -1.40):
    # Right bottom frame: v_front_out[0] (at -1.80), v_back_out[0] (at -1.40), v_front_in[0] (-1.74), v_back_in[0] (-1.40)
    # Right jamb bottom:
    bm.faces.new([s_b_tr, v_back_out[0], v_back_in[0]])
    # Left jamb bottom:
    bm.faces.new([s_b_tl, v_back_in[-1], v_back_out[-1]])

    # Exact bounding dimensions:
    # Ensure min_z = 2.050, max_z = 3.750, min_y = -1.800, max_y = -1.400, min_x = -0.450, max_x = 0.450
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    
    scale_x = 0.900 / (max_x - min_x) if (max_x - min_x) > 0 else 1.0
    scale_z = 1.700 / (max_z - min_z) if (max_z - min_z) > 0 else 1.0
    
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.z = 2.050 + (v.co.z - min_z) * scale_z

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("TowerDoorway")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("TowerDoorway", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_stone = make_material("DoorStoneArch", (0.35, 0.35, 0.36), roughness=0.8, metallic=0.0)
    mat_wood = make_material("DoorWood", (0.22, 0.13, 0.08), roughness=0.7, metallic=0.0)
    
    obj.data.materials.append(mat_stone) # 0
    obj.data.materials.append(mat_wood)  # 1
    
    return obj
