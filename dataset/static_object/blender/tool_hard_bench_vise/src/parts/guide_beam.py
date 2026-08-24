"""GuideBeam — heavy slider rail guiding sliding jaw travel.

Machined prismatic steel guide beam extending from the sliding jaw through the fixed body channel.
Material: ground oiled steel.
Plan bbox: center (0.000, -0.020, 0.075) extents (0.060, 0.220, 0.040)
  -> x in [-0.030, 0.030], y in [-0.130, 0.090], z in [0.055, 0.095]
"""
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_bright_steel_material, link_object

GUIDE_BEAM_CENTER = (0.000, -0.020, 0.075)
GUIDE_BEAM_EXTENTS = (0.060, 0.220, 0.040)

def build_guide_beam() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Prismatic guide rail with clean clearance through fixed body and sliding jaw
    # 1. Front end flange / weld plate: touches SlidingJaw
    b1 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.060, 0.002, 0.040), verts=b1["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, -0.109, 0.0)), verts=b1["verts"])
    
    # 2. Front neck section inside SlidingJaw cheeks (local Y in [-0.108, -0.060]):
    b2 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.034, 0.048, 0.036), verts=b2["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, -0.084, 0.0)), verts=b2["verts"])
    
    # 3. Main heavy slider beam through FixedBody channel (local Y in [-0.060, 0.108]):
    # Width 0.056 (x in [-0.028, 0.028]), Height 0.036 (z in [-0.018, 0.018])
    # Fits inside FixedBody channel (walls at +/-0.030, floor at z=-0.019) with 2mm clearance
    b3 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.056, 0.168, 0.036), verts=b3["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.024, 0.0)), verts=b3["verts"])
    
    # 4. Rear end cap (local Y in [0.108, 0.110]):
    b4 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.060, 0.002, 0.040), verts=b4["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.109, 0.0)), verts=b4["verts"])

    # Bevel lengthwise outer edges of main beam
    long_edges = [e for e in bm.edges if abs(e.verts[0].co.y - e.verts[1].co.y) > 0.10]
    if long_edges:
        bmesh.ops.bevel(bm, geom=long_edges, offset=0.0015, segments=2, profile=0.5, affect='EDGES')

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)

    me = bpy.data.meshes.new("GuideBeam")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("GuideBeam", me)
    obj.location = GUIDE_BEAM_CENTER
    obj.data.materials.append(get_bright_steel_material())
    return link_object(obj)
