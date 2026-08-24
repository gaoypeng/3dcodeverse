"""SnareStrainer — snare throw-off mechanism lever on side of shell."""
import bpy
import bmesh
import math
from parts._common import make_material, obj_from_bmesh

# Plan: center (-0.185, 0.000, 0.085), extents (0.032, 0.048, 0.068)
# Bounds: x in [-0.201, -0.169], y in [-0.024, 0.024], z in [0.051, 0.119]
# Note: Lugs_6 is at (-0.186, 0, 0.085) with extents (0.022, 0.024, 0.060), so y in [-0.012, +0.012].
# SnareStrainer has y extent 0.048 (y in [-0.024, +0.024]).
# If SnareStrainer has side brackets at y in [-0.024, -0.013] and [+0.013, +0.024], plus top knob at z > 0.115 and lever at x < -0.190,
# it wraps around Lugs_6 without colliding into the center block of Lugs_6!

def build_snare_strainer() -> bpy.types.Object:
    mat = make_material("SnareStrainerMat", (0.88, 0.88, 0.90), roughness=0.18, metallic=0.98)
    bm = bmesh.new()
    
    sx, sy, sz = 0.032, 0.048, 0.068
    
    # 1. Left side mounting plate (y < -0.013)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.016, 0.010, 0.044), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.000, -0.018, -0.006), verts=bm.verts)
    
    # 2. Right side mounting plate (y > +0.013)
    plate_r = bmesh.new()
    bmesh.ops.create_cube(plate_r, size=1.0)
    bmesh.ops.scale(plate_r, vec=(0.016, 0.010, 0.044), verts=plate_r.verts)
    bmesh.ops.translate(plate_r, vec=(0.000, 0.018, -0.006), verts=plate_r.verts)
    for v in plate_r.verts:
        bm.verts.new(v.co)
    bm.verts.ensure_lookup_table()
    offset = len(bm.verts) - len(plate_r.verts)
    for f in plate_r.faces:
        bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
    plate_r.free()
    
    # 3. Outer faceplate / bridge linking them in front of the lug (x < -0.008)
    bridge = bmesh.new()
    bmesh.ops.create_cube(bridge, size=1.0)
    bmesh.ops.scale(bridge, vec=(0.008, 0.044, 0.044), verts=bridge.verts)
    bmesh.ops.translate(bridge, vec=(-0.010, 0.000, -0.006), verts=bridge.verts)
    for v in bridge.verts:
        bm.verts.new(v.co)
    bm.verts.ensure_lookup_table()
    offset = len(bm.verts) - len(bridge.verts)
    for f in bridge.faces:
        bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
    bridge.free()
    
    # 4. Top adjustment dial / knob
    knob = bmesh.new()
    bmesh.ops.create_cone(
        knob,
        cap_ends=True,
        segments=20,
        radius1=0.009,
        radius2=0.009,
        depth=0.016
    )
    bmesh.ops.translate(knob, vec=(-0.004, 0.000, 0.025), verts=knob.verts)
    for v in knob.verts:
        bm.verts.new(v.co)
    bm.verts.ensure_lookup_table()
    offset = len(bm.verts) - len(knob.verts)
    for f in knob.faces:
        bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
    knob.free()
    
    # 5. Throw-off lever handle
    lever = bmesh.new()
    bmesh.ops.create_cube(lever, size=1.0)
    bmesh.ops.scale(lever, vec=(0.006, 0.008, 0.038), verts=lever.verts)
    bmesh.ops.translate(lever, vec=(-0.012, 0.014, 0.010), verts=lever.verts)
    for v in lever.verts:
        bm.verts.new(v.co)
    bm.verts.ensure_lookup_table()
    offset = len(bm.verts) - len(lever.verts)
    for f in lever.faces:
        bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
    lever.free()
    
    # Scale to match exact extents bounds (0.032, 0.048, 0.068)
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    curr_dx = max(xs) - min(xs)
    curr_dy = max(ys) - min(ys)
    curr_dz = max(zs) - min(zs)
    
    bmesh.ops.scale(bm, vec=(sx / curr_dx, sy / curr_dy, sz / curr_dz), verts=bm.verts)
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    obj = obj_from_bmesh("SnareStrainer", bm, location=(-0.185, 0.000, 0.085), material=mat)
    return obj
