"""FrontDoor — Entrance door on front wall.

Single vertical-plank timber door with rear Z-brace framing, inset into a 50 mm wide perimeter frame located on the front face (-Y).
Material: dark stained pine planks with framed surround.
Bbox: center (-0.100, -1.005, 0.925) extents (0.780, 0.050, 1.650)
  x in [-0.490, 0.290], y in [-1.030, -0.980], z in [0.100, 1.750]
"""
import bpy
import bmesh
from mathutils import Vector

def make_material(name, rgb, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_front_door() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Outer frame: x in [-0.490, 0.290], y in [-1.030, -0.980], z in [0.100, 1.750]
    # Frame width = 0.045
    fw = 0.045
    fd = 0.050
    
    # Frame Left vertical jamb
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fw, fd, 1.650), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.490 + fw/2, -1.005, 0.925), verts=bm.verts[-8:])
    
    # Frame Right vertical jamb
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(fw, fd, 1.650), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.290 - fw/2, -1.005, 0.925), verts=bm.verts[-8:])
    
    # Frame Top horizontal head jamb
    jamb_w = 0.780 - 2 * fw
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(jamb_w, fd, fw), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.100, -1.005, 1.750 - fw/2), verts=bm.verts[-8:])
    
    # Door panel: inset slightly into frame, x in [-0.490 + fw, 0.290 - fw], z in [0.100, 1.750 - fw]
    # Door panel width = 0.780 - 2*0.045 = 0.690, height = 1.650 - 0.045 = 1.605
    # Let's create vertical planks (e.g. 5 planks)
    n_planks = 5
    door_w = 0.780 - 2 * fw
    door_h = 1.650 - fw
    plank_w = door_w / n_planks
    door_t = 0.025
    door_y = -1.005 # centered in y range [-1.030, -0.980]
    
    for i in range(n_planks):
        px = (-0.490 + fw) + (i + 0.5) * plank_w
        bmesh.ops.create_cube(bm, size=1.0)
        # Leave a tiny 1mm visible groove between planks
        bmesh.ops.scale(bm, vec=(plank_w - 0.003, door_t, door_h), verts=bm.verts[-8:])
        bmesh.ops.translate(bm, vec=(px, door_y, 0.100 + door_h/2), verts=bm.verts[-8:])
        
    # Front horizontal framing ledges on the door (top and bottom horizontal ledge)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(door_w - 0.010, 0.015, 0.080), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.100, door_y - door_t/2 - 0.0075, 0.400), verts=bm.verts[-8:])
    
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(door_w - 0.010, 0.015, 0.080), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-0.100, door_y - door_t/2 - 0.0075, 1.450), verts=bm.verts[-8:])

    bm.normal_update()
    
    me = bpy.data.meshes.new("FrontDoor")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("FrontDoor", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("DarkStainedPine", (0.35, 0.22, 0.12), roughness=0.6)
    obj.data.materials.append(mat)
    return obj
