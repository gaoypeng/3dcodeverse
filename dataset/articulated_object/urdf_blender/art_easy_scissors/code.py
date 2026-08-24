"""PairOfScissors — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Detailed model of standard 21 cm household scissors:
- Brushed stainless steel beveled blades with pointed tips
- Ergonomic molded plastic handles with dual-tone grips (matte black body with red inner cushion trim)
- BaseHalf has a smaller rounder thumb loop
- PivotHalf has a larger elongated multi-finger loop
- Central brass pivot screw / rivet
- Zero mesh collision across the full joint range (0 to 60 deg opening)
- BaseHalf: centre (0.012, 0.000, 0.005) m, extents (0.065, 0.210, 0.010) m
- PivotHalf: centre (-0.012, 0.000, 0.009) m, extents (0.065, 0.210, 0.010) m
"""

import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, color, roughness=0.3, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat

mat_steel = create_material("Steel", (0.85, 0.87, 0.90, 1.0), roughness=0.2, metallic=0.95)
mat_plastic_black = create_material("PlasticBlack", (0.04, 0.04, 0.04, 1.0), roughness=0.45, metallic=0.0)
mat_plastic_red = create_material("PlasticRed", (0.85, 0.08, 0.08, 1.0), roughness=0.35, metallic=0.0)
mat_brass = create_material("Brass", (0.85, 0.65, 0.15, 1.0), roughness=0.25, metallic=0.85)

PIVOT_Y = -0.010

def make_blade_mesh(is_base=True):
    """
    Creates the steel blade.
    BaseHalf: lower blade (Z from 0.0036 to 0.0064)
    PivotHalf: upper blade (Z from 0.0076 to 0.0104)
    """
    bm = bmesh.new()
    
    z_bot = 0.0036 if is_base else 0.0076
    z_top = 0.0064 if is_base else 0.0104
    side_sign = 1.0 if is_base else -1.0
    
    # Y-stations: tip at Y = -0.105, pivot at Y = -0.010, tang extends to Y = +0.035
    y_stations = [-0.105, -0.095, -0.080, -0.060, -0.040, -0.025, -0.010, 0.005, 0.020, 0.035]
    
    station_data = []
    for y in y_stations:
        if y <= -0.010:
            # Forward blade
            t = (y - (-0.105)) / 0.095  # 0 at tip, 1 at pivot
            # Blade spine width
            w = 0.003 + 0.013 * (t ** 0.5)
            x_inner = 0.0003 * side_sign
            x_outer = x_inner + w * side_sign
        else:
            # Tang into handle
            t = (y - (-0.010)) / 0.045
            w = 0.015 - 0.004 * t
            x_center = 0.009 * side_sign
            x_inner = x_center - 0.5 * w * side_sign
            x_outer = x_center + 0.5 * w * side_sign
        station_data.append((y, x_inner, x_outer))
        
    for i in range(len(station_data) - 1):
        y0, xi0, xo0 = station_data[i]
        y1, xi1, xo1 = station_data[i+1]
        
        v_bo0 = bm.verts.new(Vector((xo0, y0, z_bot)))
        v_to0 = bm.verts.new(Vector((xo0, y0, z_top)))
        v_ti0 = bm.verts.new(Vector((xi0, y0, z_top)))
        v_bi0 = bm.verts.new(Vector((xi0, y0, z_bot)))
        
        v_bo1 = bm.verts.new(Vector((xo1, y1, z_bot)))
        v_to1 = bm.verts.new(Vector((xo1, y1, z_top)))
        v_ti1 = bm.verts.new(Vector((xi1, y1, z_top)))
        v_bi1 = bm.verts.new(Vector((xi1, y1, z_bot)))
        
        bm.faces.new([v_bo0, v_bo1, v_bi1, v_bi0])
        bm.faces.new([v_to0, v_ti0, v_ti1, v_to1])
        bm.faces.new([v_bo0, v_to0, v_to1, v_bo1])
        bm.faces.new([v_bi0, v_bi1, v_ti1, v_ti0])
        
        if i == 0:
            bm.faces.new([v_bo0, v_bi0, v_ti0, v_to0])
        if i == len(station_data) - 2:
            bm.faces.new([v_bo1, v_to1, v_ti1, v_bi1])

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    
    bm.normal_update()
    me = bpy.data.meshes.new("BladeMesh")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("BladeObj", me)
    obj.data.materials.append(mat_steel)
    return obj

def make_handle_mesh(is_base=True):
    """
    Creates ergonomic molded plastic handle.
    BaseHalf: Small round thumb loop on +X side.
    PivotHalf: Large elongated multi-finger loop on -X side.
    """
    bm = bmesh.new()
    
    # BaseHalf: Z = 0.000 to 0.0064
    # PivotHalf: Z = 0.0076 to 0.0140
    z_loop_bot = 0.000 if is_base else 0.0076
    z_loop_top = 0.0064 if is_base else 0.0140
    
    n_pts = 40
    outer_pts = []
    inner_pts = []
    
    if is_base:
        # Base Half: Smaller, rounder Thumb loop
        # Center of thumb hole
        center_x = 0.0240
        center_y = 0.0650
        rx_in, ry_in = 0.0100, 0.0150  # Compact inner thumb opening (20mm x 30mm)
        rx_out, ry_out = 0.0195, 0.0280 # Outer boundary around loop
        
        for i in range(n_pts):
            theta = 2.0 * math.pi * i / n_pts
            ct = math.cos(theta)
            st = math.sin(theta)
            
            # Inner hole is a smooth ellipse
            xi = center_x + rx_in * ct
            yi = center_y + ry_in * st
            
            # Outer contour: ellipse around loop, smoothly extended down at the front towards shank (Y=0.010)
            xo = center_x + rx_out * ct
            yo = center_y + ry_out * st
            
            # Forward shank blending (angles pointing toward -Y, i.e. theta around -pi/2 / 3*pi/2)
            # theta around 1.3*pi to 1.7*pi
            if 1.25 * math.pi <= theta <= 1.75 * math.pi:
                t = (theta - 1.25 * math.pi) / (0.5 * math.pi)
                blend = math.sin(t * math.pi)
                yo -= 0.026 * blend
                xo -= 0.012 * blend
                
            # Back rest contour to extend smoothly to Y = 0.105
            if 0.25 * math.pi <= theta <= 0.75 * math.pi:
                t = (theta - 0.25 * math.pi) / (0.5 * math.pi)
                blend = math.sin(t * math.pi)
                yo += 0.012 * blend
                xo += 0.002 * blend
                
            outer_pts.append((xo, yo))
            inner_pts.append((xi, yi))
    else:
        # Pivot Half: Large, elongated Multi-finger loop
        # Center of finger hole
        center_x = -0.0240
        center_y = 0.0650
        rx_in, ry_in = 0.0110, 0.0280  # Elongated inner finger opening (22mm x 56mm)
        rx_out, ry_out = 0.0205, 0.0400 # Elongated outer boundary reaching Y=0.105
        
        for i in range(n_pts):
            theta = 2.0 * math.pi * i / n_pts
            ct = math.cos(theta)
            st = math.sin(theta)
            
            # Inner hole: elongated ellipse
            xi = center_x + rx_in * ct
            yi = center_y + ry_in * st
            
            # Outer contour
            xo = center_x + rx_out * ct
            yo = center_y + ry_out * st
            
            # Forward shank blending toward front (Y=0.010)
            if 1.25 * math.pi <= theta <= 1.75 * math.pi:
                t = (theta - 1.25 * math.pi) / (0.5 * math.pi)
                blend = math.sin(t * math.pi)
                yo -= 0.015 * blend
                xo += 0.012 * blend
                
            outer_pts.append((xo, yo))
            inner_pts.append((xi, yi))
            
    verts_top_out = [bm.verts.new(Vector((x, y, z_loop_top))) for x, y in outer_pts]
    verts_bot_out = [bm.verts.new(Vector((x, y, z_loop_bot))) for x, y in outer_pts]
    verts_top_in  = [bm.verts.new(Vector((x, y, z_loop_top))) for x, y in inner_pts]
    verts_bot_in  = [bm.verts.new(Vector((x, y, z_loop_bot))) for x, y in inner_pts]
    
    for i in range(n_pts):
        i_next = (i + 1) % n_pts
        # Outer wall
        bm.faces.new([verts_bot_out[i], verts_bot_out[i_next], verts_top_out[i_next], verts_top_out[i]])
        # Inner wall
        bm.faces.new([verts_bot_in[i], verts_top_in[i], verts_top_in[i_next], verts_bot_in[i_next]])
        # Top cap
        bm.faces.new([verts_top_out[i], verts_top_out[i_next], verts_top_in[i_next], verts_top_in[i]])
        # Bottom cap
        bm.faces.new([verts_bot_out[i], verts_bot_in[i], verts_bot_in[i_next], verts_bot_out[i_next]])

    # Shank overmold bridge connecting handle loop to blade tang (Y = 0.005 to 0.038)
    neck_x = 0.010 if is_base else -0.010
    h_neck = z_loop_top - z_loop_bot
    z_neck_mid = (z_loop_bot + z_loop_top) / 2.0
    
    neck_bm = bmesh.ops.create_cube(bm, size=1.0)
    neck_verts = neck_bm['verts']
    bmesh.ops.scale(bm, vec=Vector((0.012, 0.034, h_neck)), verts=neck_verts)
    bmesh.ops.translate(bm, vec=Vector((neck_x, 0.022, z_neck_mid)), verts=neck_verts)
    
    bm.normal_update()
    
    me = bpy.data.meshes.new("HandleMesh")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("HandleObj", me)
    obj.data.materials.append(mat_plastic_black)
    return obj

def make_handle_cushion(is_base=True):
    """Red inner rubber cushion lining inside the handle loop."""
    bm = bmesh.new()
    z_bot = (0.000 if is_base else 0.0076) + 0.0004
    z_top = (0.0064 if is_base else 0.0140) - 0.0004
    
    n_pts = 40
    outer_pts = []
    inner_pts = []
    
    if is_base:
        # BaseHalf thumb cushion
        center_x = 0.0240
        center_y = 0.0650
        rx_out, ry_out = 0.0104, 0.0154
        rx_in, ry_in = 0.0084, 0.0128
    else:
        # PivotHalf multi-finger cushion
        center_x = -0.0240
        center_y = 0.0650
        rx_out, ry_out = 0.0114, 0.0284
        rx_in, ry_in = 0.0092, 0.0252
        
    for i in range(n_pts):
        theta = 2.0 * math.pi * i / n_pts
        ct = math.cos(theta)
        st = math.sin(theta)
        outer_pts.append((center_x + rx_out * ct, center_y + ry_out * st))
        inner_pts.append((center_x + rx_in * ct, center_y + ry_in * st))
        
    verts_top_out = [bm.verts.new(Vector((x, y, z_top))) for x, y in outer_pts]
    verts_bot_out = [bm.verts.new(Vector((x, y, z_bot))) for x, y in outer_pts]
    verts_top_in  = [bm.verts.new(Vector((x, y, z_top))) for x, y in inner_pts]
    verts_bot_in  = [bm.verts.new(Vector((x, y, z_bot))) for x, y in inner_pts]
    
    for i in range(n_pts):
        i_next = (i + 1) % n_pts
        bm.faces.new([verts_bot_out[i], verts_bot_out[i_next], verts_top_out[i_next], verts_top_out[i]])
        bm.faces.new([verts_bot_in[i], verts_top_in[i], verts_top_in[i_next], verts_bot_in[i_next]])
        bm.faces.new([verts_top_out[i], verts_top_out[i_next], verts_top_in[i_next], verts_top_in[i]])
        bm.faces.new([verts_bot_out[i], verts_bot_in[i], verts_bot_in[i_next], verts_bot_out[i_next]])

    bm.normal_update()
    me = bpy.data.meshes.new("CushionMesh")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("CushionObj", me)
    obj.data.materials.append(mat_plastic_red)
    return obj

def make_screw_mesh(is_base=True):
    """
    Pivot screw parts:
    BaseHalf: bottom rivet/flange (Z = 0.000 to 0.0035).
    PivotHalf: top screw head (Z = 0.0105 to 0.0140).
    """
    bm = bmesh.new()
    if is_base:
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=20,
            radius1=0.0042,
            radius2=0.0048,
            depth=0.0030,
            matrix=Matrix.Translation(Vector((0.0, PIVOT_Y, 0.0018)))
        )
    else:
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=20,
            radius1=0.0048,
            radius2=0.0042,
            depth=0.0030,
            matrix=Matrix.Translation(Vector((0.0, PIVOT_Y, 0.0122)))
        )
    
    me = bpy.data.meshes.new("ScrewMesh")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new("ScrewObj", me)
    obj.data.materials.append(mat_brass)
    return obj

# 1. Build BaseHalf
blade_base = make_blade_mesh(is_base=True)
handle_base = make_handle_mesh(is_base=True)
cushion_base = make_handle_cushion(is_base=True)
screw_base = make_screw_mesh(is_base=True)

for o in [blade_base, handle_base, cushion_base, screw_base]:
    bpy.context.scene.collection.objects.link(o)

bpy.ops.object.select_all(action='DESELECT')
for o in [blade_base, handle_base, cushion_base, screw_base]:
    o.select_set(True)
bpy.context.view_layer.objects.active = blade_base
bpy.ops.object.join()
base_half = bpy.context.active_object
base_half.name = 'base_half'

# 2. Build PivotHalf
blade_pivot = make_blade_mesh(is_base=False)
handle_pivot = make_handle_mesh(is_base=False)
cushion_pivot = make_handle_cushion(is_base=False)
screw_pivot = make_screw_mesh(is_base=False)

for o in [blade_pivot, handle_pivot, cushion_pivot, screw_pivot]:
    bpy.context.scene.collection.objects.link(o)

bpy.ops.object.select_all(action='DESELECT')
for o in [blade_pivot, handle_pivot, cushion_pivot, screw_pivot]:
    o.select_set(True)
bpy.context.view_layer.objects.active = blade_pivot
bpy.ops.object.join()
pivot_half = bpy.context.active_object
pivot_half.name = 'pivot_half'

# Smooth shading
for obj in [base_half, pivot_half]:
    for poly in obj.data.polygons:
        poly.use_smooth = True

print("Finished building scissors models.")
