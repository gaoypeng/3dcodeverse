"""TommyBarHandle — sliding T-handle bar for turning the lead screw.

Solid cylindrical steel bar (diameter 12 mm, length 180 mm) sliding horizontally through a cross-hole in the lead screw collar, terminated on both ends with 18 mm spherical retaining ball caps.
Material: polished chrome steel.
Plan bbox: center (0.000, -0.175, 0.115) extents (0.180, 0.020, 0.020)
  -> x in [-0.090, 0.090], y in [-0.185, -0.165], z in [0.105, 0.125]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_chrome_material, link_object

TOMMY_BAR_HANDLE_CENTER = (0.000, -0.175, 0.115)
TOMMY_BAR_HANDLE_EXTENTS = (0.180, 0.020, 0.020)

def build_tommy_bar_handle() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Bar length 0.164 m, radius 0.006 m
    bar_length = 0.164
    bar_r = 0.006
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=20,
        radius1=bar_r,
        radius2=bar_r,
        depth=bar_length,
        matrix=Matrix.Rotation(math.pi / 2, 4, 'Y')
    )

    cap_r = 0.009
    for sign in [-1, 1]:
        bmesh.ops.create_uvsphere(
            bm,
            u_segments=20,
            v_segments=12,
            radius=cap_r,
            matrix=Matrix.Translation((sign * (0.090 - cap_r), 0.0, 0.0))
        )

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)

    me = bpy.data.meshes.new("TommyBarHandle")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("TommyBarHandle", me)
    obj.location = TOMMY_BAR_HANDLE_CENTER
    obj.data.materials.append(get_chrome_material())
    return link_object(obj)
