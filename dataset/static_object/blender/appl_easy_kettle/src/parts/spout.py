"""Spout — tapered pouring spout protruding from the front (part module; imported by src/model.py).

Triangular-profile angled spout opening outwards and upwards at the front (-Y), wall thickness 1.5 mm, joining seamlessly into the top front rim of the kettle body.
Material: brushed stainless steel.  Instances: 1.  Attaches to: KettleBody (must touch, no gap).

Exports `build_spout() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

# Plan numbers
# center (0.000, -0.078, 0.170) extents (0.046, 0.044, 0.056)
# x in [-0.023, 0.023]  y in [-0.100, -0.056]  z in [0.142, 0.198]


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_spout() -> bpy.types.Object:
    bm = bmesh.new()

    # The kettle body around z in [0.142, 0.194] has radius:
    # at z=0.142: r ≈ 0.061  (y ≈ -0.061)
    # at z=0.170: r = 0.058  (y = -0.058)
    # at z=0.194: r = 0.056  (y = -0.056)
    # To reduce interpenetration from ~4.9mm to ~1.5mm while keeping the bounding box intact,
    # the root vertices conform to the body surface (y ≈ -sqrt(r(z)^2 - x^2) - 0.001)
    # while maintaining bounding box x in [-0.023, 0.023], y in [-0.100, -0.056], z in [0.142, 0.198].

    n_seg = 16
    
    # Slices along parameter t from 0 (root/body interface) to 1 (pouring tip)
    t_steps = [0.0, 0.2, 0.45, 0.7, 0.9, 1.0]
    
    rings = []
    for t in t_steps:
        # Center z of this cross section:
        # base center ~ 0.170, tip center ~ 0.188
        cz = (1 - t) * 0.170 + t * 0.188
        
        # Extents in x and z
        rx = (1 - t) * 0.023 + t * 0.011
        rz_bottom = (1 - t) * 0.028 + t * 0.010
        rz_top = (1 - t) * 0.026 + t * 0.010
        
        # Y position along the spout
        # at t=1, y = -0.100
        # at t=0, y is close to the body surface around -0.056 to -0.060
        y_nominal = (1 - t) * (-0.057) + t * (-0.100)
        
        ring = []
        for i in range(n_seg):
            angle = 2 * math.pi * i / n_seg
            ca = math.cos(angle)
            sa = math.sin(angle)
            
            # Form an inverted triangular / teardrop shape:
            local_x = ca * rx * (0.85 + 0.15 * sa if sa < 0 else 1.0)
            local_z = sa * (rz_top if sa >= 0 else rz_bottom)
            
            vx = max(-0.023, min(0.023, local_x))
            vz = max(0.142, min(0.198, cz + local_z))
            
            if t == 0.0:
                # Calculate body surface radius at this z:
                # Kettle body profile: z=0.12 -> r=0.064; z=0.17 -> r=0.058; z=0.188 -> r=0.056; z=0.194 -> r=0.056
                if vz >= 0.188:
                    body_r = 0.056
                elif vz >= 0.170:
                    body_r = 0.058 - (vz - 0.170) / (0.188 - 0.170) * (0.058 - 0.056)
                elif vz >= 0.120:
                    body_r = 0.064 - (vz - 0.120) / (0.170 - 0.120) * (0.064 - 0.058)
                else:
                    body_r = 0.0685
                
                # Cylinder surface y for given vx
                under_sqrt = max(0.0, body_r**2 - vx**2)
                surface_y = -math.sqrt(under_sqrt)
                # Overlap by 1.2 mm into the body for clean weld without large interpenetration
                vy = surface_y + 0.0012
                # Ensure vy does not exceed -0.056 to stay in plan bbox
                vy = min(-0.056, vy)
            else:
                vy = y_nominal
                
            ring.append(bm.verts.new((vx, vy, vz)))
        rings.append(ring)

    # Bridge rings
    for i in range(len(rings) - 1):
        r1 = rings[i]
        r2 = rings[i + 1]
        for j in range(n_seg):
            jn = (j + 1) % n_seg
            bm.faces.new((r1[j], r1[jn], r2[jn], r2[j]))

    # Root cap conforming to body
    rc_z = 0.170
    rc_r = 0.058
    rc = bm.verts.new((0.0, -rc_r + 0.0012, rc_z))
    for j in range(n_seg):
        jn = (j + 1) % n_seg
        bm.faces.new((rc, rings[0][jn], rings[0][j]))

    # Tip opening / mouth
    tc = bm.verts.new((0.0, -0.098, 0.188))
    for j in range(n_seg):
        jn = (j + 1) % n_seg
        bm.faces.new((tc, rings[-1][j], rings[-1][jn]))

    bm.normal_update()

    me = bpy.data.meshes.new("Spout")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("Spout", me)
    bpy.context.scene.collection.objects.link(obj)

    for poly in me.polygons:
        poly.use_smooth = True

    mat = make_material("SpoutSteel", (0.78, 0.79, 0.80), roughness=0.25, metallic=0.95)
    obj.data.materials.append(mat)

    return obj
