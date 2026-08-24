"""GableWalls — Front and rear triangular gable wall peaks.

Two triangular gable wall extensions continuing the horizontal cladding from wall plate height (1.85 m) up to the roof ridge apex (2.30 m).
Material: natural cedar horizontal weatherboard.  Instances: 2 (mirror_y).
Bbox: center (0.000, 0.000, 2.075) extents (1.800, 2.000, 0.450)
  x in [-0.900, 0.900], y in [-1.000, 1.000], z in [1.850, 2.300]
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

def _build_single_gable(name: str, y_center: float, y_thick: float, mat) -> bpy.types.Object:
    # A triangular prism:
    # Base at z = 1.850, x in [-0.900, 0.900]
    # Apex at z = 2.300, x = 0.000
    # Thickness along Y = y_thick
    bm = bmesh.new()
    
    y0 = y_center - y_thick / 2
    y1 = y_center + y_thick / 2
    
    # 6 vertices of a triangular prism
    # Front triangle (y = y0):
    v0 = bm.verts.new(Vector((-0.900, y0, 1.850)))
    v1 = bm.verts.new(Vector(( 0.900, y0, 1.850)))
    v2 = bm.verts.new(Vector(( 0.000, y0, 2.300)))
    
    # Back triangle (y = y1):
    v3 = bm.verts.new(Vector((-0.900, y1, 1.850)))
    v4 = bm.verts.new(Vector(( 0.900, y1, 1.850)))
    v5 = bm.verts.new(Vector(( 0.000, y1, 2.300)))
    
    # Faces
    bm.faces.new((v0, v1, v2))          # y0 face
    bm.faces.new((v5, v4, v3))          # y1 face
    bm.faces.new((v0, v3, v4, v1))      # bottom face
    bm.faces.new((v1, v4, v5, v2))      # right slope face
    bm.faces.new((v2, v5, v3, v0))      # left slope face
    
    # Horizontal plank line details on the outer face
    n_slats = 5
    for i in range(n_slats):
        sz = 1.850 + (i + 0.5) * (0.450 / n_slats)
        # width at this height: z goes from 1.85 to 2.30 (height 0.45), full width 1.80 down to 0
        w = 1.800 * (1.0 - (sz - 1.850) / 0.450)
        if w > 0.05:
            bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=(w, 0.008, 0.075), verts=bm.verts[-8:])
            bmesh.ops.translate(bm, vec=(0.0, y_center, sz), verts=bm.verts[-8:])

    bm.normal_update()
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj

def build_gable_walls() -> list[bpy.types.Object]:
    mat = make_material("CedarGableWeatherboard", (0.72, 0.48, 0.28), roughness=0.65)
    
    wall_thick = 0.06
    # Front gable: y around -1.000 + wall_thick/2 = -0.970 (or aligned with front wall)
    # Rear gable: y around 1.000 - wall_thick/2 = +0.970
    # Note: total bbox span across the 2 instances must cover y in [-1.000, 1.000]
    # So front gable sits at y in [-1.000, -1.000 + wall_thick], y_center = -1.000 + wall_thick/2
    # Rear gable sits at y in [1.000 - wall_thick, 1.000], y_center = 1.000 - wall_thick/2
    g0 = _build_single_gable("GableWalls_0", -1.000 + wall_thick / 2, wall_thick, mat)
    g1 = _build_single_gable("GableWalls_1",  1.000 - wall_thick / 2, wall_thick, mat)
    return [g0, g1]
