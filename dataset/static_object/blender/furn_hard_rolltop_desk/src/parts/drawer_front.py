"""DrawerFront — visible drawer facade panels.

Set of 3 stacked drawer fronts per pedestal (6 total) with realistic tight reveal gaps around each opening.
Material: polished mahogany wood, warm dark brown. Instances: 6 (mirror_x).
"""
import bpy
import bmesh

# Pedestal opening height is 0.665 m (from plinth top z = 0.060 to underside of top z = 0.725).
# Carcass cavity in PedestalCabinet is z in [0.050, 0.710].
# 3 drawers of height 0.210 m each = 0.630 m total height.
# Gaps: bottom reveal 8 mm, drawer-drawer gaps 6 mm, top reveal 8 mm.
# Drawer 0 (bottom): z_min = 0.068, z_max = 0.278 -> center z = 0.173
# Drawer 1 (middle): z_min = 0.284, z_max = 0.494 -> center z = 0.389
# Drawer 2 (top):    z_min = 0.500, z_max = 0.710 -> center z = 0.605
DRAWER_FRONT_EXTENTS = (0.340, 0.022, 0.210)

DRAWER_POSITIONS = [
    # Right pedestal (x = +0.480)
    ( 0.480, -0.335, 0.605),  # top right
    ( 0.480, -0.335, 0.389),  # mid right
    ( 0.480, -0.335, 0.173),  # bot right
    # Left pedestal (x = -0.480)
    (-0.480, -0.335, 0.605),  # top left
    (-0.480, -0.335, 0.389),  # mid left
    (-0.480, -0.335, 0.173),  # bot left
]

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_drawer_front():
    mat = make_material("MahoganyDrawerFront", (0.30, 0.13, 0.05), roughness=0.32, metallic=0.0)
    objs = []
    
    for i, pos in enumerate(DRAWER_POSITIONS):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=DRAWER_FRONT_EXTENTS, verts=bm.verts)
        
        me = bpy.data.meshes.new(f"DrawerFront_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"DrawerFront_{i}", me)
        obj.location = pos
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = 0.002
        mod.segments = 2
        mod.limit_method = "ANGLE"
        
        objs.append(obj)
        
    return objs
