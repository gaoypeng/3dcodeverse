"""GuitarBody — main resonant hollow body (part module; imported by src/model.py).

Figure-eight hollow acoustic body with wide lower bout (w=0.38m), narrow waist (w=0.27m), and upper bout (w=0.30m); curved wooden sides with solid front soundboard and slightly arched back, 100 mm total depth.
Material: natural spruce top, varnished mahogany back and sides.  Instances: 1.

Plan bbox: center (0.000, 0.000, 0.250) extents (0.380, 0.100, 0.500)
x in [-0.190, 0.190], y in [-0.050, 0.050], z in [0.000, 0.500]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

GUITAR_BODY_CENTER = (0.000, 0.000, 0.250)
GUITAR_BODY_EXTENTS = (0.380, 0.100, 0.500)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def get_body_half_width(z):
    # Overall z in [0.000, 0.500]
    # z = 0.000: bottom base (flat tangent at bottom center)
    # z = 0.140: lower bout max half-width = 0.190 (width 0.380)
    # z = 0.300: waist min half-width = 0.135 (width 0.270)
    # z = 0.410: upper bout max half-width = 0.150 (width 0.300)
    # z = 0.500: top shoulder at neck joint, half-width = 0.065
    
    if z <= 0.0:
        return 0.0
    if z >= 0.500:
        return 0.065
    
    if z < 0.140:
        u = z / 0.140
        return 0.190 * math.sqrt(max(0.0, 1.0 - (1.0 - u)**2))
    elif z < 0.300:
        u = (z - 0.140) / (0.300 - 0.140)
        f = 0.5 * (1.0 + math.cos(u * math.pi))
        return 0.135 + (0.190 - 0.135) * f
    elif z < 0.410:
        u = (z - 0.300) / (0.410 - 0.300)
        f = 0.5 * (1.0 - math.cos(u * math.pi))
        return 0.135 + (0.150 - 0.135) * f
    else:
        u = (z - 0.410) / (0.500 - 0.410)
        return 0.065 + (0.150 - 0.065) * math.sqrt(max(0.0, 1.0 - u**2))

def build_guitar_body():
    bm = bmesh.new()
    
    num_z_steps = 32
    # Vertices arranged in a 2D grid of rows across Z, columns across X (-w(z) to +w(z))
    cols = 16  # number of points across the width
    
    # Create front grid (y = -0.050)
    front_grid = []
    for i in range(num_z_steps):
        z = i * 0.500 / (num_z_steps - 1)
        w = get_body_half_width(z)
        row = []
        for j in range(cols):
            # t from -1 to +1
            t = -1.0 + 2.0 * j / (cols - 1)
            x = t * w
            v = bm.verts.new((x, -0.050, z))
            row.append(v)
        front_grid.append(row)
        
    # Create back grid (y = +0.050)
    back_grid = []
    for i in range(num_z_steps):
        z = i * 0.500 / (num_z_steps - 1)
        w = get_body_half_width(z)
        row = []
        for j in range(cols):
            t = -1.0 + 2.0 * j / (cols - 1)
            x = t * w
            v = bm.verts.new((x, 0.050, z))
            row.append(v)
        back_grid.append(row)
        
    bm.verts.ensure_lookup_table()
    
    # Front quads (facing -Y)
    for i in range(num_z_steps - 1):
        for j in range(cols - 1):
            v0 = front_grid[i][j]
            v1 = front_grid[i][j+1]
            v2 = front_grid[i+1][j+1]
            v3 = front_grid[i+1][j]
            bm.faces.new([v0, v3, v2, v1])
            
    # Back quads (facing +Y)
    for i in range(num_z_steps - 1):
        for j in range(cols - 1):
            v0 = back_grid[i][j]
            v1 = back_grid[i][j+1]
            v2 = back_grid[i+1][j+1]
            v3 = back_grid[i+1][j]
            bm.faces.new([v0, v1, v2, v3])
            
    # Side quads:
    # Right side (j = cols - 1)
    for i in range(num_z_steps - 1):
        f0 = front_grid[i][cols-1]
        f1 = front_grid[i+1][cols-1]
        b0 = back_grid[i][cols-1]
        b1 = back_grid[i+1][cols-1]
        bm.faces.new([f0, f1, b1, b0])
        
    # Left side (j = 0)
    for i in range(num_z_steps - 1):
        f0 = front_grid[i][0]
        f1 = front_grid[i+1][0]
        b0 = back_grid[i][0]
        b1 = back_grid[i+1][0]
        bm.faces.new([f0, b0, b1, f1])
        
    # Top rim (i = num_z_steps - 1)
    for j in range(cols - 1):
        f0 = front_grid[num_z_steps-1][j]
        f1 = front_grid[num_z_steps-1][j+1]
        b0 = back_grid[num_z_steps-1][j]
        b1 = back_grid[num_z_steps-1][j+1]
        bm.faces.new([f0, f1, b1, b0])
        
    # Bottom rim (i = 0)
    for j in range(cols - 1):
        f0 = front_grid[0][j]
        f1 = front_grid[0][j+1]
        b0 = back_grid[0][j]
        b1 = back_grid[0][j+1]
        bm.faces.new([f0, b0, b1, f1])
        
    # Merge duplicate vertices at collapsed ends if any
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("GuitarBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GuitarBody", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Spruce top / warm mahogany wood material
    mat_top = make_material("SpruceTop", (0.84, 0.69, 0.48), roughness=0.35, metallic=0.0)
    obj.data.materials.append(mat_top)
    
    return obj
