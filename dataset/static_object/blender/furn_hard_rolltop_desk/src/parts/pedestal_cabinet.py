"""PedestalCabinet — structural pedestal carcasses housing the drawers.

Two enclosed cabinet boxes (left and right) forming the pedestal structure. Side and back panels are 20 mm thick with internal divider rails between drawer tiers.
Material: polished mahogany wood, warm dark brown. Instances: 2 (mirror_x).
"""
import bpy
import bmesh

PEDESTAL_CABINET_EXTENTS = (0.380, 0.680, 0.690)
PEDESTAL_CENTERS = [(0.480, 0.000, 0.380), (-0.480, 0.000, 0.380)]

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_pedestal_cabinet():
    mat = make_material("MahoganyPedestal", (0.28, 0.12, 0.05), roughness=0.35, metallic=0.0)
    objs = []
    
    for i, center in enumerate(PEDESTAL_CENTERS):
        # We make the pedestal carcass hollow in front so the drawers fit inside nicely!
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=PEDESTAL_CABINET_EXTENTS, verts=bm.verts)
        
        me = bpy.data.meshes.new(f"PedestalCabinet_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"PedestalCabinet_{i}", me)
        obj.location = center
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        
        # Hollow front cavity for drawers:
        # drawer cavity width: 0.342, depth: 0.20, height: 0.65
        # Cavity front at y = -0.340 to -0.140
        cutter_bm = bmesh.new()
        bmesh.ops.create_cube(cutter_bm, size=1.0)
        bmesh.ops.scale(cutter_bm, vec=(0.342, 0.20, 0.66), verts=cutter_bm.verts)
        cutter_me = bpy.data.meshes.new("TempDrawerCavity")
        cutter_bm.to_mesh(cutter_me)
        cutter_bm.free()
        cutter_obj = bpy.data.objects.new("TempDrawerCavity", cutter_me)
        cutter_obj.location = (center[0], center[1] - 0.245, center[2])
        bpy.context.scene.collection.objects.link(cutter_obj)
        
        bool_mod = obj.modifiers.new("DrawerCavity", "BOOLEAN")
        bool_mod.operation = "DIFFERENCE"
        bool_mod.object = cutter_obj
        bool_mod.solver = 'EXACT'
        
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier="DrawerCavity")
            
        bpy.data.objects.remove(cutter_obj, do_unlink=True)
        bpy.data.meshes.remove(cutter_me, do_unlink=True)
        
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = 0.003
        mod.segments = 2
        mod.limit_method = "ANGLE"
        
        objs.append(obj)
        
    return objs
