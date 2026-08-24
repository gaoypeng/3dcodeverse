import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def link(obj: bpy.types.Object) -> bpy.types.Object:
    bpy.context.scene.collection.objects.link(obj)
    return obj

def obj_from_bmesh(name: str, bm: bmesh.types.BMesh, location=(0, 0, 0)) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    return link(obj)

def make_material(name: str, rgb=(0.18, 0.09, 0.04), roughness=0.38, metallic=0.0) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def add_material_to_obj(obj, mat):
    obj.data.materials.append(mat)

# -----------------------------------------------------------------------------
# Part Builders
# -----------------------------------------------------------------------------

def build_seat(mat) -> bpy.types.Object:
    """
    Seat: main sitting surface
    Plan bbox: center (0.000, -0.010, 0.445) extents (0.460, 0.440, 0.035)
    World Z bounds: [0.4275, 0.4625]
    """
    bm = bmesh.new()
    nx = 24
    ny = 24
    
    verts_top = []
    verts_bot = []
    
    for iy in range(ny):
        v_y = iy / (ny - 1)  # 0 to 1 (front to back)
        y_pos = -0.230 + v_y * 0.440
        
        # Mid-century organic contour
        half_w = 0.230 * (1.0 - 0.07 * (v_y ** 1.4))
        if abs(v_y - 0.2) < 0.05:
            half_w = 0.230
            
        for ix in range(nx):
            v_x = ix / (nx - 1)
            u_x = -1.0 + 2.0 * v_x
            x_pos = u_x * half_w
            
            # Ergonomic saddle dish:
            dish1 = math.exp(-(((x_pos - 0.085) / 0.08)**2 + ((y_pos - (-0.02)) / 0.11)**2)) * 0.0055
            dish2 = math.exp(-(((x_pos + 0.085) / 0.08)**2 + ((y_pos - (-0.02)) / 0.11)**2)) * 0.0055
            pommel = math.exp(-((x_pos / 0.05)**2 + ((y_pos - (-0.14)) / 0.07)**2)) * 0.002
            
            front_roll = math.exp(-((v_y) / 0.15)**2) * 0.004
            edge_f = abs(u_x)**3 * 0.003
            
            z_top = 0.0175 - (dish1 + dish2) + pommel - front_roll - edge_f
            z_bot = -0.0175 + 0.002 * (abs(u_x)**2)
            
            # Mortise hole for Stiles: around x = ±0.170, y_pos = 0.160
            # If (x,y) is inside the stile footprint (radius 0.016), push top surface down to z_bot + 0.001
            for sx in (-0.170, 0.170):
                d_sq = (x_pos - sx)**2 + (y_pos - 0.160)**2
                if d_sq < (0.0165)**2:
                    # Carve a through-mortise cavity in the seat mesh
                    z_top = z_bot + 0.001
                elif d_sq < (0.020)**2:
                    t_trans = (math.sqrt(d_sq) - 0.0165) / (0.020 - 0.0165)
                    z_top = (z_bot + 0.001) * (1 - t_trans) + z_top * t_trans
            
            vt = bm.verts.new((x_pos, y_pos - (-0.010), z_top))
            vb = bm.verts.new((x_pos, y_pos - (-0.010), z_bot))
            verts_top.append(vt)
            verts_bot.append(vb)
            
    for iy in range(ny - 1):
        for ix in range(nx - 1):
            i0 = iy * nx + ix
            i1 = iy * nx + (ix + 1)
            i2 = (iy + 1) * nx + (ix + 1)
            i3 = (iy + 1) * nx + ix
            bm.faces.new((verts_top[i0], verts_top[i3], verts_top[i2], verts_top[i1]))
            bm.faces.new((verts_bot[i0], verts_bot[i1], verts_bot[i2], verts_bot[i3]))
            
    for ix in range(nx - 1):
        bm.faces.new((verts_top[ix], verts_top[ix + 1], verts_bot[ix + 1], verts_bot[ix]))
        i0 = (ny - 1) * nx + ix
        i1 = (ny - 1) * nx + (ix + 1)
        bm.faces.new((verts_top[i1], verts_top[i0], verts_bot[i0], verts_bot[i1]))
    for iy in range(ny - 1):
        i0 = iy * nx
        i1 = (iy + 1) * nx
        bm.faces.new((verts_top[i1], verts_top[i0], verts_bot[i0], verts_bot[i1]))
        i0_r = iy * nx + (nx - 1)
        i1_r = (iy + 1) * nx + (nx - 1)
        bm.faces.new((verts_top[i0_r], verts_top[i1_r], verts_bot[i1_r], verts_bot[i0_r]))
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = obj_from_bmesh("Seat", bm, location=(0.000, -0.010, 0.445))
    
    # Exact bounding box fit
    me = obj.data
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    sx = 0.460 / (max(xs) - min(xs))
    sy = 0.440 / (max(ys) - min(ys))
    sz = 0.035 / (max(zs) - min(zs))
    for v in me.vertices:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z *= sz
    me.update()
    
    sub = obj.modifiers.new("Subsurf", "SUBSURF")
    sub.levels = 1
    sub.render_levels = 1
    
    add_material_to_obj(obj, mat)
    return obj


def build_seat_frame_apron(mat) -> bpy.types.Object:
    """
    SeatFrameApron: structural under-seat apron box
    Plan bbox: center (0.000, -0.010, 0.410) extents (0.400, 0.380, 0.035)
    World bounds: x in [-0.200, 0.200], y in [-0.200, 0.180], z in [0.3925, 0.4275]
    """
    bm = bmesh.new()
    ow, od, h, t = 0.400, 0.380, 0.035, 0.020
    
    # Front rail:
    rail_w_front = 0.336
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(rail_w_front, t, h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0, -od/2 + t/2, 0), verts=bm.verts[-8:])
    
    # Back rail:
    rail_w_back = 0.316
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(rail_w_back, t, h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0, od/2 - t/2, 0), verts=bm.verts[-8:])
    
    # Left rail
    rail_d = 0.290
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(t, rail_d, h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-ow/2 + t/2, 0, 0), verts=bm.verts[-8:])
    
    # Right rail
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(t, rail_d, h), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(ow/2 - t/2, 0, 0), verts=bm.verts[-8:])
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = obj_from_bmesh("SeatFrameApron", bm, location=(0.000, -0.010, 0.410))
    
    # Exact bounding box fit to plan (0.400, 0.380, 0.035)
    me = obj.data
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    sx = ow / (max(xs) - min(xs))
    sy = od / (max(ys) - min(ys))
    sz = h / (max(zs) - min(zs))
    for v in me.vertices:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z *= sz
    me.update()
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    add_material_to_obj(obj, mat)
    return obj


def build_tapered_leg(name: str, center: tuple, extents: tuple, top_r: float, bot_r: float, splay_x: float, splay_y: float, mat) -> bpy.types.Object:
    """
    Tapered Scandinavian leg: 36mm top down to 22mm bottom, splayed gracefully.
    Top of leg terminates at z = 0.420.
    """
    bm = bmesh.new()
    segments = 24
    height = extents[2]
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=bot_r,
        radius2=top_r,
        depth=height
    )
    
    for v in bm.verts:
        factor = (height/2 - v.co.z) / height
        v.co.x += factor * splay_x
        v.co.y += factor * splay_y
        
    obj = obj_from_bmesh(name, bm, location=center)
    
    me = obj.data
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    sx = extents[0] / (max(xs) - min(xs))
    sy = extents[1] / (max(ys) - min(ys))
    sz = extents[2] / (max(zs) - min(zs))
    for v in me.vertices:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z *= sz
    me.update()
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0015
    bev.segments = 2
    
    add_material_to_obj(obj, mat)
    return obj


def build_front_legs(mat) -> list:
    legs = []
    leg0 = build_tapered_leg(
        "FrontLeg_0",
        center=(0.190, -0.170, 0.210),
        extents=(0.038, 0.038, 0.420),
        top_r=0.018,
        bot_r=0.011,
        splay_x=0.008,
        splay_y=-0.008,
        mat=mat
    )
    legs.append(leg0)
    
    leg1 = build_tapered_leg(
        "FrontLeg_1",
        center=(-0.190, -0.170, 0.210),
        extents=(0.038, 0.038, 0.420),
        top_r=0.018,
        bot_r=0.011,
        splay_x=-0.008,
        splay_y=-0.008,
        mat=mat
    )
    legs.append(leg1)
    return legs


def build_back_legs(mat) -> list:
    legs = []
    leg0 = build_tapered_leg(
        "BackLeg_0",
        center=(0.180, 0.160, 0.210),
        extents=(0.038, 0.038, 0.420),
        top_r=0.018,
        bot_r=0.011,
        splay_x=0.008,
        splay_y=0.010,
        mat=mat
    )
    legs.append(leg0)
    
    leg1 = build_tapered_leg(
        "BackLeg_1",
        center=(-0.180, 0.160, 0.210),
        extents=(0.038, 0.038, 0.420),
        top_r=0.018,
        bot_r=0.011,
        splay_x=-0.008,
        splay_y=0.010,
        mat=mat
    )
    legs.append(leg1)
    return legs


def build_backrest_stiles(mat) -> list:
    """
    BackrestStile x2: vertical backrest support spine
    Round upright spindle rising from rear seat up to the curved backrest rail.
    Plan bbox: center (0.170, 0.160, 0.600) extents (0.032, 0.032, 0.380)
    Span z is [0.410, 0.790].
    """
    stiles = []
    for idx, sign_x in enumerate([1.0, -1.0]):
        name = f"BackrestStile_{idx}"
        bm = bmesh.new()
        
        n_pts = 30
        n_circ = 20
        verts_ring = []
        
        for ip in range(n_pts):
            t_p = ip / (n_pts - 1)  # 0 at bottom (z=0.410), 1 at top (z=0.790)
            world_z = 0.410 + t_p * 0.380
            loc_z = world_z - 0.600
            
            # Splay / tilt backward (+Y):
            tilt_y = t_p * 0.012
            tilt_x = -sign_x * t_p * 0.003
            
            # Radius profile:
            if world_z < 0.460:
                rad = 0.015
            elif world_z > 0.745:
                rad = 0.012
                tilt_y -= 0.006 * ((world_z - 0.745) / 0.045)
            else:
                rad = 0.015 - 0.003 * ((world_z - 0.460) / 0.285)
                
            ring = []
            for ic in range(n_circ):
                ang = 2 * math.pi * ic / n_circ
                vx = tilt_x + rad * math.cos(ang)
                vy = tilt_y + rad * math.sin(ang)
                v = bm.verts.new((vx, vy, loc_z))
                ring.append(v)
            verts_ring.append(ring)
            
        for ip in range(n_pts - 1):
            for ic in range(n_circ):
                ic_next = (ic + 1) % n_circ
                v0 = verts_ring[ip][ic]
                v1 = verts_ring[ip][ic_next]
                v2 = verts_ring[ip + 1][ic_next]
                v3 = verts_ring[ip + 1][ic]
                bm.faces.new((v0, v1, v2, v3))
                
        # Caps
        bm.faces.new(verts_ring[0][::-1])
        bm.faces.new(verts_ring[-1])
        
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        obj = obj_from_bmesh(name, bm, location=(sign_x * 0.170, 0.160, 0.600))
        
        me = obj.data
        xs = [v.co.x for v in me.vertices]
        ys = [v.co.y for v in me.vertices]
        zs = [v.co.z for v in me.vertices]
        sx = 0.032 / (max(xs) - min(xs))
        sy = 0.032 / (max(ys) - min(ys))
        sz = 0.380 / (max(zs) - min(zs))
        for v in me.vertices:
            v.co.x *= sx
            v.co.y *= sy
            v.co.z *= sz
        me.update()
        
        bev = obj.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.0015
        bev.segments = 2
        
        add_material_to_obj(obj, mat)
        stiles.append(obj)
        
    return stiles


def build_curved_backrest(mat) -> bpy.types.Object:
    """
    CurvedBackrest: upper lumbar and thoracic support rail
    Steam-bent curved backrest plank with a concave forward arc embracing the seated person.
    Plan bbox: center (0.000, 0.190, 0.750) extents (0.440, 0.080, 0.100)
      x in [-0.220, 0.220], y in [0.150, 0.230], z in [0.700, 0.800]
    """
    bm = bmesh.new()
    n_arc = 32
    n_z = 10
    thickness = 0.020
    
    verts_front = []
    verts_back = []
    
    for iz in range(n_z):
        v_z = -1.0 + 2.0 * (iz / (n_z - 1))
        
        for ia in range(n_arc):
            u_a = -1.0 + 2.0 * (ia / (n_arc - 1))
            x = u_a * 0.220
            
            # Concave curve forward (-Y in Blender frame):
            # Center of backrest is back (+Y), ends sweep forward (-Y)
            y_curve = 0.035 - (u_a ** 2) * 0.070
            
            # Ergonomic height taper: 100 mm in center down to 70 mm at ends
            h_factor = 1.0 - 0.30 * (abs(u_a) ** 1.8)
            z = v_z * 0.050 * h_factor
            
            y_curve += v_z * 0.006
            
            dy_dx = -2.0 * 0.070 * u_a / 0.220
            norm_angle = math.atan(dy_dx)
            nx_val = -math.sin(norm_angle)
            ny_val = math.cos(norm_angle)
            
            half_t = thickness / 2.0
            if abs(u_a) > 0.80:
                tip_f = (abs(u_a) - 0.80) / 0.20
                half_t *= (1.0 - 0.35 * tip_f)
                
            vf = bm.verts.new((x - half_t * nx_val, y_curve - half_t * ny_val, z))
            vb = bm.verts.new((x + half_t * nx_val, y_curve + half_t * ny_val, z))
            verts_front.append(vf)
            verts_back.append(vb)
            
    for iz in range(n_z - 1):
        for ia in range(n_arc - 1):
            i0 = iz * n_arc + ia
            i1 = iz * n_arc + (ia + 1)
            i2 = (iz + 1) * n_arc + (ia + 1)
            i3 = (iz + 1) * n_arc + ia
            bm.faces.new((verts_front[i0], verts_front[i1], verts_front[i2], verts_front[i3]))
            bm.faces.new((verts_back[i0], verts_back[i3], verts_back[i2], verts_back[i1]))
            
    for ia in range(n_arc - 1):
        i0 = (n_z - 1) * n_arc + ia
        i1 = (n_z - 1) * n_arc + (ia + 1)
        bm.faces.new((verts_front[i0], verts_front[i1], verts_back[i1], verts_back[i0]))
        i0_b = ia
        i1_b = ia + 1
        bm.faces.new((verts_front[i1_b], verts_front[i0_b], verts_back[i0_b], verts_back[i1_b]))
        
    for iz in range(n_z - 1):
        i0 = iz * n_arc
        i1 = (iz + 1) * n_arc
        bm.faces.new((verts_front[i0], verts_front[i1], verts_back[i1], verts_back[i0]))
        i0_r = iz * n_arc + (n_arc - 1)
        i1_r = (iz + 1) * n_arc + (n_arc - 1)
        bm.faces.new((verts_front[i1_r], verts_front[i0_r], verts_back[i0_r], verts_back[i1_r]))
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = obj_from_bmesh("CurvedBackrest", bm, location=(0.000, 0.190, 0.750))
    
    me = obj.data
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0
    cz = (max(zs) + min(zs)) / 2.0
    
    # Scale to fill exactly 0.440 x 0.080 x 0.100 while keeping boundary crisp
    sx = 0.440 / (max(xs) - min(xs))
    sy = 0.080 / (max(ys) - min(ys))
    sz = 0.100 / (max(zs) - min(zs))
    
    for v in me.vertices:
        v.co.x = (v.co.x - cx) * sx
        v.co.y = (v.co.y - cy) * sy
        v.co.z = (v.co.z - cz) * sz
    me.update()
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0025
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    add_material_to_obj(obj, mat)
    return obj


def build_side_stretchers(mat) -> list:
    """
    SideStretcher x2: lateral leg brace
    Slender cylindrical stretcher rod spanning horizontally between front and rear legs.
    Plan bbox: center (0.185, -0.005, 0.160) extents (0.020, 0.330, 0.020)
    """
    stretchers = []
    for idx, sign_x in enumerate([1.0, -1.0]):
        name = f"SideStretcher_{idx}"
        bm = bmesh.new()
        segments = 24
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=segments,
            radius1=0.008,
            radius2=0.008,
            depth=0.330
        )
        
        rot_matrix = Matrix.Rotation(math.radians(90.0), 4, 'X')
        bmesh.ops.transform(bm, matrix=rot_matrix, verts=bm.verts)
        
        for v in bm.verts:
            y_norm = abs(v.co.y) / (0.330 / 2.0)
            swell = 1.0 - 0.10 * (y_norm ** 2)
            v.co.x *= swell
            v.co.z *= swell
            
        obj = obj_from_bmesh(name, bm, location=(sign_x * 0.185, -0.005, 0.160))
        
        me = obj.data
        xs = [v.co.x for v in me.vertices]
        ys = [v.co.y for v in me.vertices]
        zs = [v.co.z for v in me.vertices]
        sx = 0.020 / (max(xs) - min(xs))
        sy = 0.330 / (max(ys) - min(ys))
        sz = 0.020 / (max(zs) - min(zs))
        for v in me.vertices:
            v.co.x *= sx
            v.co.y *= sy
            v.co.z *= sz
        me.update()
        
        bev = obj.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.0015
        bev.segments = 2
        
        add_material_to_obj(obj, mat)
        stretchers.append(obj)
        
    return stretchers


# -----------------------------------------------------------------------------
# Main Assembly
# -----------------------------------------------------------------------------

def main():
    mat = make_material("WalnutWood", (0.18, 0.09, 0.04), roughness=0.38, metallic=0.0)
    build_seat(mat)
    build_seat_frame_apron(mat)
    build_front_legs(mat)
    build_back_legs(mat)
    build_backrest_stiles(mat)
    build_curved_backrest(mat)
    build_side_stretchers(mat)

main()
