"""FresnelBeacon — central optical lens and rotating light assembly.
Tiered concentric glass Fresnel lens drum with brass armature and glowing central halogen lamp mounted on a pedestal at center of lantern room,
diameter 0.75 m, height 1.25 m.
BBox: center (0.000, 0.000, 10.950) extents (0.750, 0.750, 1.250)
z in [10.325, 11.575], radius = 0.375.
"""
import math
import bpy
import bmesh
from mathutils import Vector

FRESNEL_BEACON_CENTER = (0.000, 0.000, 10.950)
FRESNEL_BEACON_EXTENTS = (0.750, 0.750, 1.250)

def make_material(name, rgb, roughness=0.2, metallic=0.0, emission=None, alpha=1.0, transmission=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 5.0
    if transmission > 0:
        bsdf.inputs["Transmission Weight"].default_value = transmission
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = alpha
        mat.blend_method = 'BLEND'
    return mat

def build_fresnel_beacon() -> bpy.types.Object:
    bm = bmesh.new()
    segments = 32
    
    # Structure:
    # 1. Brass Pedestal / Stand Base: extending down to GalleryDeck at z=10.098 to weld with GalleryDeck (deck at z=10.10)
    # 2. Fresnel Lens Drum (ribbed glass + brass frame rings): z from 10.60 to 11.45 (dia 0.75m -> r=0.375)
    # 3. Glowing central lamp inside: cylinder from 10.80 to 11.20 (r=0.08)
    # 4. Top Brass Cap / Ventilator: z from 11.45 to 11.575 (dia 0.40m)
    
    def add_cylinder_mesh(z_bot, z_top, r_bot, r_top, mat_idx, capped=True):
        r1 = []
        r2 = []
        for i in range(segments):
            ang = 2 * math.pi * i / segments
            r1.append(bm.verts.new((r_bot * math.cos(ang), r_bot * math.sin(ang), z_bot)))
            r2.append(bm.verts.new((r_top * math.cos(ang), r_top * math.sin(ang), z_top)))
        for i in range(segments):
            next_i = (i + 1) % segments
            f = bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            f.material_index = mat_idx
        if capped:
            cb = bm.verts.new((0, 0, z_bot))
            ct = bm.verts.new((0, 0, z_top))
            for i in range(segments):
                next_i = (i + 1) % segments
                f1 = bm.faces.new([r1[next_i], r1[i], cb])
                f2 = bm.faces.new([r2[i], r2[next_i], ct])
                f1.material_index = mat_idx
                f2.material_index = mat_idx

    # Pedestal extending down to touch GalleryDeck (10.098 m)
    # Flanged mounting base at bottom
    add_cylinder_mesh(10.098, 10.150, 0.30, 0.26, mat_idx=0, capped=True)
    # Pedestal column (brass)
    add_cylinder_mesh(10.150, 10.600, 0.22, 0.24, mat_idx=0, capped=True)
    
    # Glowing center light bulb (Mat 2: Emissive Lamp)
    add_cylinder_mesh(10.800, 11.200, 0.08, 0.08, mat_idx=2, capped=True)
    
    # Stepped Fresnel Lens Rings (Mat 1: Lens Glass): 4 concentric rings
    z_lens_levels = [10.60, 10.75, 10.95, 11.15, 11.30, 11.45]
    r_lens_levels = [0.34, 0.375, 0.375, 0.375, 0.36, 0.32]
    
    for l_idx in range(len(z_lens_levels) - 1):
        zb = z_lens_levels[l_idx]
        zt = z_lens_levels[l_idx + 1]
        rb = r_lens_levels[l_idx]
        rt = r_lens_levels[l_idx + 1]
        add_cylinder_mesh(zb, zt, rb, rt, mat_idx=1, capped=False)
        
    # Brass Lens Rings (Top and Bottom bezels)
    add_cylinder_mesh(10.590, 10.615, 0.35, 0.35, mat_idx=0, capped=True)
    add_cylinder_mesh(11.435, 11.460, 0.33, 0.33, mat_idx=0, capped=True)
    
    # Top Cap (Brass)
    add_cylinder_mesh(11.460, 11.575, 0.20, 0.05, mat_idx=0, capped=True)

    # Ensure exact extents (x, y)
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    
    scale_x = 0.750 / (max_x - min_x)
    scale_y = 0.750 / (max_y - min_y)
    
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("FresnelBeacon")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("FresnelBeacon", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_brass = make_material("BeaconBrass", (0.85, 0.65, 0.20), roughness=0.25, metallic=0.9)
    mat_glass = make_material("FresnelGlass", (0.95, 0.98, 1.00), roughness=0.1, metallic=0.0, alpha=0.4, transmission=0.85)
    mat_emissive = make_material("BeaconLamp", (1.0, 0.95, 0.7), roughness=0.1, metallic=0.0, emission=(1.0, 0.95, 0.6))
    
    obj.data.materials.append(mat_brass)    # 0
    obj.data.materials.append(mat_glass)    # 1
    obj.data.materials.append(mat_emissive) # 2
    
    return obj

