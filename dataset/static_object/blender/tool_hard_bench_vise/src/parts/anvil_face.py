"""AnvilFace — machined flat striking anvil surface.

Solid rectangular steel block integrated into the top surface of the fixed body behind the rear jaw, with a precision ground top surface (70×80 mm) and chamfered perimeter edges.
Material: machined bright steel.
Plan bbox: center (0.000, 0.090, 0.145) extents (0.070, 0.080, 0.015)
  -> x in [-0.035, 0.035], y in [0.050, 0.130], z in [0.1375, 0.1525]
"""
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_bright_steel_material, link_object

ANVIL_FACE_CENTER = (0.000, 0.090, 0.145)
ANVIL_FACE_EXTENTS = (0.070, 0.080, 0.015)

def build_anvil_face() -> bpy.types.Object:
    bm = bmesh.new()
    # Create cube of exact extents
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=ANVIL_FACE_EXTENTS, verts=bm.verts)
    
    # Slight chamfer / bevel on top edges
    # Top face is at local z = +0.015 / 2 = 0.0075
    top_verts = [v for v in bm.verts if v.co.z > 0.005]
    top_edges = [e for e in bm.edges if all(v in top_verts for v in e.verts)]
    
    if top_edges:
        bmesh.ops.bevel(bm, geom=top_edges, offset=0.002, segments=1, profile=0.5, affect='EDGES')

    me = bpy.data.meshes.new("AnvilFace")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("AnvilFace", me)
    obj.location = ANVIL_FACE_CENTER
    obj.data.materials.append(get_bright_steel_material())
    return link_object(obj)
