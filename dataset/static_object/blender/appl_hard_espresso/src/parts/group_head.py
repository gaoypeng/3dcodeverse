"""GroupHead — hot water dispersion block and portafilter mounting collar.

Polished chrome cylindrical brew group collar (diameter 76 mm) protruding downward from the upper overhang cavity.
Material: polished chrome brass.
Plan bbox: center (0.000, -0.090, 0.220) extents (0.080, 0.080, 0.040)
  x in [-0.040, 0.040], y in [-0.130, -0.050], z in [0.200, 0.240]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.1, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_group_head():
    """GroupHead: stepped cylindrical brass/chrome group collar (E61-style / prosumer collar)."""
    bm = bmesh.new()
    
    # 1. Main outer collar ring (diameter 76mm -> radius 0.038m, height 0.025m)
    # z: 0.215 to 0.240 (welds into chassis underside at 0.238)
    v_start = len(bm.verts)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=36,
        radius1=0.038,
        radius2=0.038,
        depth=0.025
    )
    bmesh.ops.translate(bm, vec=(0.000, -0.090, 0.2275), verts=bm.verts[v_start:])
    
    # 2. Lower lock flange ring (diameter 80mm -> radius 0.040m, height 0.015m)
    # z: 0.200 to 0.215
    v_start = len(bm.verts)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=36,
        radius1=0.035,
        radius2=0.040,
        depth=0.015
    )
    bmesh.ops.translate(bm, vec=(0.000, -0.090, 0.2075), verts=bm.verts[v_start:])
    
    # 3. Inner shower screen indent
    v_start = len(bm.verts)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=24,
        radius1=0.028,
        radius2=0.028,
        depth=0.008
    )
    bmesh.ops.translate(bm, vec=(0.000, -0.090, 0.204), verts=bm.verts[v_start:])
    
    me = bpy.data.meshes.new("GroupHead")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GroupHead", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("ChromeGroupHead", (0.95, 0.95, 0.95), roughness=0.1, metallic=1.0)
    obj.data.materials.append(mat)
    
    return obj
