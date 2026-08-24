"""SlotLiners — Internal metal heating chamber slot guides.

Pair of deep rectangular cavity inserts representing internal heating elements and wire bread racks descending into the body.
Material: galvanized steel with faint mica heater texture.  Instances: 2 (mirror_y).  Attaches to: TopTrim.
Plan bbox: center (0.000, -0.032, 0.135) extents (0.140, 0.030, 0.100)
  x in [-0.070, 0.070]  y in [-0.047, -0.017]  z in [0.085, 0.185]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

SLOT_LINERS_CENTER = (0.000, -0.032, 0.135)
SLOT_LINERS_EXTENTS = (0.140, 0.030, 0.100)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_hollow_box(bm, sx, sy, sz, wall=0.002):
    """Creates a hollow open-top box along Z, with walls and floor, top open."""
    # Outer box
    hx = sx / 2.0
    hy = sy / 2.0
    hz = sz / 2.0
    
    # Outer box cube
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    
    # Inner cutter to hollow it out
    inner_bm = bmesh.new()
    bmesh.ops.create_cube(inner_bm, size=1.0)
    # Inner dimensions: open at top (so scale z extends slightly above top)
    bmesh.ops.scale(inner_bm, vec=(sx - 2*wall, sy - 2*wall, sz), verts=inner_bm.verts)
    bmesh.ops.translate(inner_bm, vec=(0, 0, wall), verts=inner_bm.verts)
    
    # Let's do direct bmesh difference or construct walls directly:
    inner_bm.free()

def build_slot_liner_mesh(sx, sy, sz, wall=0.002):
    bm = bmesh.new()
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    
    # Construct an open-top hollow box with 5 faces (outer) and 5 faces (inner)
    # Or simply: outer box, delete top face, solidify modifier
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    
    # Remove top face
    top_faces = [f for f in bm.faces if abs(f.normal.z - 1.0) < 0.1]
    bmesh.ops.delete(bm, geom=top_faces, context='FACES_ONLY')
    
    return bm

def build_slot_liners():
    mat = make_material("GalvanizedSteel", (0.65, 0.65, 0.67), roughness=0.45, metallic=0.9)
    objs = []
    
    sx, sy, sz = SLOT_LINERS_EXTENTS
    positions = [(0.0, 0.032, 0.135), (0.0, -0.032, 0.135)]
    
    for i, c in enumerate(positions):
        bm = build_slot_liner_mesh(sx, sy, sz, wall=0.002)
        me = bpy.data.meshes.new(f"SlotLiners_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"SlotLiners_{i}", me)
        obj.location = c
        bpy.context.scene.collection.objects.link(obj)
        
        # Solidify to give metal sheet thickness
        sol = obj.modifiers.new("Solidify", "SOLIDIFY")
        sol.thickness = 0.002
        sol.offset = -1.0
        
        bev = obj.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.001
        bev.segments = 2
        bev.limit_method = "ANGLE"
        
        obj.data.materials.append(mat)
        objs.append(obj)
        
    return objs
