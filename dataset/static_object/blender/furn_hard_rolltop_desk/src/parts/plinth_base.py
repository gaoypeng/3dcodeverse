"""PlinthBase — bottom moulded foundation plinth under each pedestal.

Moulded perimeter skirt plinth supporting each pedestal directly on the ground plane, 60 mm high with a stepped top cove profile extending 15 mm past pedestal box.
Material: polished mahogany wood, warm dark brown. Instances: 2 (mirror_x).
"""
import bpy
import bmesh

PLINTH_BASE_EXTENTS = (0.410, 0.710, 0.060)
PLINTH_CENTERS = [(0.480, 0.000, 0.030), (-0.480, 0.000, 0.030)]

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_plinth_base():
    mat = make_material("MahoganyPlinth", (0.26, 0.11, 0.04), roughness=0.35, metallic=0.0)
    objs = []
    
    for i, center in enumerate(PLINTH_CENTERS):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=PLINTH_BASE_EXTENTS, verts=bm.verts)
        
        me = bpy.data.meshes.new(f"PlinthBase_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"PlinthBase_{i}", me)
        obj.location = center
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        
        # Cutter object for the recessed top where pedestal sits
        cutter_bm = bmesh.new()
        bmesh.ops.create_cube(cutter_bm, size=1.0)
        # Size slightly larger than pedestal in Z to cleanly cut open top face
        bmesh.ops.scale(cutter_bm, vec=(0.3804, 0.6804, 0.030), verts=cutter_bm.verts)
        cutter_me = bpy.data.meshes.new("TempCutter")
        cutter_bm.to_mesh(cutter_me)
        cutter_bm.free()
        cutter_obj = bpy.data.objects.new("TempCutter", cutter_me)
        # Cutter center at top half of plinth: z = 0.0155 local -> world z = 0.030 + 0.0155 = 0.0455
        cutter_obj.location = (center[0], center[1], center[2] + 0.0155)
        bpy.context.scene.collection.objects.link(cutter_obj)
        
        # Apply boolean difference
        bool_mod = obj.modifiers.new("HollowTop", "BOOLEAN")
        bool_mod.operation = "DIFFERENCE"
        bool_mod.object = cutter_obj
        bool_mod.solver = 'EXACT'
        
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier="HollowTop")
            
        bpy.data.objects.remove(cutter_obj, do_unlink=True)
        bpy.data.meshes.remove(cutter_me, do_unlink=True)
        
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = 0.003
        mod.segments = 2
        mod.limit_method = "ANGLE"
        
        objs.append(obj)
        
    return objs
