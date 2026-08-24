"""HammerEye — central mounting block of the steel head.

Rectangular forged steel eye block with rounded side cheeks.
Material: forged carbon steel, semi-polished gunmetal grey.  Instances: 1.  Attaches to: WoodenHandle.
Plan bbox: center (0.000, 0.000, 0.305) extents (0.032, 0.036, 0.045)
  x in [-0.016, 0.016]  y in [-0.018, 0.018]  z in [0.2825, 0.3275]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

HAMMER_EYE_CENTER = (0.000, 0.000, 0.305)
HAMMER_EYE_EXTENTS = (0.032, 0.036, 0.045)

def make_material(name, rgb, roughness=0.35, metallic=0.9):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_hammer_eye():
    """Build HammerEye block with socket cavity matching handle tenon with 0.5 mm gap/overlap."""
    bm = bmesh.new()
    
    # Outer profile: rounded rectangular cross section
    def get_outer_ring(scale_x=1.0, scale_y=1.0, z=0.0):
        pts = []
        n_corner = 4
        rx, ry = 0.016 * scale_x, 0.018 * scale_y
        cr = 0.005
        bx = rx - cr
        by = ry - cr
        centers = [(bx, by), (-bx, by), (-bx, -by), (bx, -by)]
        base_angles = [0, math.pi/2, math.pi, 3*math.pi/2]
        
        for c, (cx, cy) in enumerate(centers):
            ba = base_angles[c]
            for s in range(n_corner):
                ang = ba + (math.pi/2) * (s / n_corner)
                x = cx + cr * math.cos(ang)
                y = cy + cr * math.sin(ang)
                pts.append((x, y, z))
        return pts

    # Inner socket:
    # From z=0.2825 to z=0.310, handle tenon has rx=0.0118, ry=0.0133.
    # We make socket rx=0.0120, ry=0.0135 (0.2 mm clearance, touches WoodenHandle at shoulder).
    # From z=0.310 to z=0.3275, socket closes or fills under TopWedge so TopWedge (z in [0.326, 0.330]) touches the floor/walls at z=0.3275!
    # Or socket has a shelf at z=0.3275.
    
    z_levels = [0.2825, 0.2855, 0.3050, 0.3100, 0.3245, 0.3275]
    scales = [
        (0.90, 0.90),
        (1.00, 1.00),
        (1.00, 1.00),
        (1.00, 1.00),
        (1.00, 1.00),
        (0.90, 0.90),
    ]
    
    # Outer mesh
    outer_rings = []
    for z, (sx, sy) in zip(z_levels, scales):
        pts = get_outer_ring(sx, sy, z)
        outer_rings.append([bm.verts.new(p) for p in pts])
        
    # Bottom socket ring at z=0.2825, mid socket ring at z=0.3100
    # Then floor at z=0.3100 or closed top at 0.3275
    # Let's create an inner socket from z=0.2825 to z=0.3100:
    n_pts = len(outer_rings[0])
    
    def get_socket_pts(z):
        pts = []
        rx, ry = 0.0120, 0.0135
        for i in range(n_pts):
            ang = 2.0 * math.pi * i / n_pts
            pts.append((rx * math.cos(ang), ry * math.sin(ang), z))
        return pts

    sock_bot = [bm.verts.new(p) for p in get_socket_pts(0.2825)]
    sock_top = [bm.verts.new(p) for p in get_socket_pts(0.3100)]
    
    # Bottom annulus: between outer_rings[0] and sock_bot
    for i in range(n_pts):
        next_i = (i + 1) % n_pts
        bm.faces.new([outer_rings[0][i], sock_bot[i], sock_bot[next_i], outer_rings[0][next_i]])
        
    # Socket inner walls: between sock_bot and sock_top
    for i in range(n_pts):
        next_i = (i + 1) % n_pts
        bm.faces.new([sock_bot[i], sock_top[i], sock_top[next_i], sock_bot[next_i]])
        
    # Socket floor at z=0.3100
    bm.faces.new(sock_top)
    
    # Outer side walls
    for r in range(len(outer_rings) - 1):
        r1 = outer_rings[r]
        r2 = outer_rings[r + 1]
        for i in range(n_pts):
            next_i = (i + 1) % n_pts
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            
    # Solid top cap at z=0.3275 (faces +Z)
    bm.faces.new(outer_rings[-1])
    
    bm.normal_update()
    me = bpy.data.meshes.new("HammerEye")
    bm.to_mesh(me)
    bm.free()
    
    for poly in me.polygons:
        poly.use_smooth = True
        
    obj = bpy.data.objects.new("HammerEye", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("GunmetalSteel", (0.50, 0.52, 0.54), roughness=0.35, metallic=0.92)
    obj.data.materials.append(mat)
    
    return obj
