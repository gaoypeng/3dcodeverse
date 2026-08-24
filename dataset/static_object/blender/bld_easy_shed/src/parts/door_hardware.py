"""DoorHardware — Hinges and T-handle latch for door.

Two black iron decorative strap hinges mounted horizontally across the door face and a rustic black metal lever/padlock latch handle.
Material: wrought iron, matte black textured metal.
Bbox: center (-0.100, -1.035, 0.925) extents (0.700, 0.030, 1.400)
  x in [-0.450, 0.250], y in [-1.050, -1.020], z in [0.225, 1.625]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.4, metallic=0.9):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_door_hardware() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Target bbox: x in [-0.450, 0.250] (ext 0.700), y in [-1.050, -1.020] (ext 0.030), z in [0.225, 1.625] (ext 1.400)
    # y center = -1.035
    # Let y span from -1.050 to -1.020
    
    # 1. Top Hinge (strap + pintle)
    # Strap: x in [-0.430, 0.050], y in [-1.030, -1.020], z in [1.425, 1.475]
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.480, 0.010, 0.050), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.190, -1.025, 1.450), verts=bm.verts[-8:])
    
    # Top Pintle: x in [-0.450, -0.410], y in [-1.045, -1.020], z in [1.275, 1.625]
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.040, 0.025, 0.350), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.430, -1.0325, 1.450), verts=bm.verts[-8:])
    
    # 2. Bottom Hinge (strap + pintle)
    # Strap: x in [-0.430, 0.050], y in [-1.030, -1.020], z in [0.375, 0.425]
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.480, 0.010, 0.050), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.190, -1.025, 0.400), verts=bm.verts[-8:])
    
    # Bottom Pintle: x in [-0.450, -0.410], y in [-1.045, -1.020], z in [0.225, 0.575]
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.040, 0.025, 0.350), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.430, -1.0325, 0.400), verts=bm.verts[-8:])
    
    # 3. Latch and Handle assembly on the right
    # Mounting plate: x in [0.170, 0.250] (center 0.210), y in [-1.030, -1.020], z in [0.825, 1.025] (center 0.925)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.010, 0.200), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.210, -1.025, 0.925), verts=bm.verts[-8:])
    
    # Handle stem & grip: y extends to -1.050
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.020, 0.020, 0.020), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.210, -1.040, 0.925), verts=bm.verts[-8:])
    
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.020, 0.010, 0.140), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.210, -1.045, 0.925), verts=bm.verts[-8:])

    bm.normal_update()
    
    me = bpy.data.meshes.new("DoorHardware")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("DoorHardware", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("WroughtIronMatteBlack", (0.10, 0.10, 0.11), roughness=0.4, metallic=0.85)
    obj.data.materials.append(mat)
    return obj
