```python
import bpy
import bmesh
import math
from mathutils import Vector

# ---------------------------------------------------------------------------
# Dimensions (meters)
# ---------------------------------------------------------------------------
STOOL_HEIGHT = 0.45          # overall height (top of seat rim)
SEAT_RADIUS = 0.17           # round seat, 34 cm diameter
SEAT_THICKNESS = 0.035
SEAT_TOP_Z = STOOL_HEIGHT
SEAT_BOT_Z = SEAT_TOP_Z - SEAT_THICKNESS
SEAT_EDGE_ROUND = 0.012      # rounded top edge radius
SEAT_BOT_CHAMFER = 0.004
SEAT_DISH_DEPTH = 0.006      # gentle concave dish in the seat top

LEG_TOP_Z = SEAT_BOT_Z + 0.017   # leg tops are embedded into the seat underside
LEG_RADIAL_TOP = 0.085           # distance of leg axis from stool axis at the top
LEG_RADIAL_BOT = 0.150           # ... and at the floor (splay outward)
LEG_R_TOP = 0.019                # leg thickness radius at the top
LEG_R_BOT = 0.0155               # slightly tapered toward the foot

SEAT_SEGS = 64
LEG_SEGS = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_material(name, color, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = nt.nodes.get("Material Output")
        if out is None:
            out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    mat.diffuse_color = (color[0], color[1], color[2], 1.0)
    mat.roughness = roughness
    mat.metallic = metallic
    return mat


def new_object(name, bm, mat):
    """Turn a bmesh into a uniquely named mesh object linked to the scene collection."""
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    old_me = bpy.data.meshes.get(name)
    if old_me is not None:
        bpy.data.meshes.remove(old_me)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()
    obj = bpy.data.objects.new(name, me)
    obj.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def lathe(profile, segs):
    """Surface of revolution around Z. profile = [(r, z), ...]; r == 0 -> center vertex."""
    bm = bmesh.new()
    rings = []
    for (r, z) in profile:
        if r <= 1e-9:
            rings.append([bm.verts.new((0.0, 0.0, z))])
        else:
            ring = []
            for i in range(segs):
                a = 2.0 * math.pi * i / segs
                ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), z)))
            rings.append(ring)
    bm.verts.ensure_lookup_table()
    for k in range(len(rings) - 1):
        a = rings[k]
        b = rings[k + 1]
        if len(a) == 1 and len(b) == 1:
            continue
        if len(a) == 1:
            c = a[0]
            for i in range(segs):
                j = (i + 1) % segs
                bm.faces.new((c, b[i], b[j]))
        elif len(b) == 1:
            c = b[0]
            for i in range(segs):
                j = (i + 1) % segs
                bm.faces.new((a[i], a[j], c))
        else:
            for i in range(segs):
                j = (i + 1) % segs
                bm.faces.new((a[i], a[j], b[j], b[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = True
    return bm


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------
def seat_profile():
    R = SEAT_RADIUS
    b = SEAT_EDGE_ROUND
    c = SEAT_BOT_CHAMFER
    d = SEAT_DISH_DEPTH
    rd = R - b - 0.015          # radius of the dished area
    prof = []
    # gently dished top (center is lowest, rim of the dish is at full height)
    n_dish = 6
    for i in range(n_dish + 1):
        r = rd * i / n_dish
        z = SEAT_TOP_Z - d * (1.0 - (r / rd) ** 2)
        prof.append((r, z))
    # flat band out to where the rounded edge starts
    prof.append((R - b, SEAT_TOP_Z))
    # rounded top edge
    for ang in (67.5, 45.0, 22.5, 0.0):
        a = math.radians(ang)
        prof.append((R - b + b * math.cos(a), SEAT_TOP_Z - b + b * math.sin(a)))
    # vertical rim, small bottom chamfer, flat bottom
    prof.append((R, SEAT_BOT_Z + c))
    prof.append((R - c, SEAT_BOT_Z))
    prof.append((0.0, SEAT_BOT_Z))
    return prof


def make_seat(mat):
    bm = lathe(seat_profile(), SEAT_SEGS)
    return new_object("Seat", bm, mat)


def make_leg(name, phi, mat):
    """Splayed tapered round leg. Horizontal cross-sections: flat foot at z=0, flat top."""
    top_c = Vector((LEG_RADIAL_TOP * math.cos(phi), LEG_RADIAL_TOP * math.sin(phi), LEG_TOP_Z))
    bot_c = Vector((LEG_RADIAL_BOT * math.cos(phi), LEG_RADIAL_BOT * math.sin(phi), 0.0))
    bm = bmesh.new()
    nrings = 6
    rings = []
    for k in range(nrings):
        t = k / (nrings - 1)
        c = bot_c.lerp(top_c, t)
        r = LEG_R_BOT * (1.0 - t) + LEG_R_TOP * t
        ring = []
        for i in range(LEG_SEGS):
            a = 2.0 * math.pi * i / LEG_SEGS
            ring.append(bm.verts.new((c.x + r * math.cos(a), c.y + r * math.sin(a), c.z)))
        rings.append(ring)
    bm.verts.ensure_lookup_table()
    # sides
    for k in range(nrings - 1):
        a = rings[k]
        b = rings[k + 1]
        for i in range(LEG_SEGS):
            j = (i + 1) % LEG_SEGS
            f = bm.faces.new((a[i], a[j], b[j], b[i]))
            f.smooth = True
    # flat caps (foot on the ground at z=0, top embedded in the seat)
    fb = bm.faces.new(rings[0])
    ft = bm.faces.new(rings[-1])
    fb.smooth = False
    ft.smooth = False
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in (fb, ft):
        for e in f.edges:
            e.smooth = False
    return new_object(name, bm, mat)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def main():
    wood_seat = make_material("WoodSeat", (0.58, 0.38, 0.19), roughness=0.45, metallic=0.0)
    wood_leg = make_material("WoodLeg", (0.46, 0.28, 0.13), roughness=0.5, metallic=0.0)

    make_seat(wood_seat)

    # Three legs, 120 degrees apart: one at the back (+Y), two at the front (-Y).
    legs = {
        "LegBack": 90.0,
        "LegFrontLeft": 210.0,
        "LegFrontRight": 330.0,
    }
    for name, deg in legs.items():
        make_leg(name, math.radians(deg), wood_leg)


main()
```