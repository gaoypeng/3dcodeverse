```python
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

SEG = 64


# ---------------------------------------------------------------- materials
def make_material(name, color, roughness=0.5, metallic=0.0, emission=None, emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (color[0], color[1], color[2], 1.0)
    mat.roughness = roughness
    mat.metallic = metallic
    try:
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = None
        for n in nt.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                bsdf = n
                break
        if bsdf is None:
            bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
            out = None
            for n in nt.nodes:
                if n.type == 'OUTPUT_MATERIAL':
                    out = n
                    break
            if out is None:
                out = nt.nodes.new('ShaderNodeOutputMaterial')
            nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
        bsdf.inputs['Base Color'].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['Metallic'].default_value = metallic
        if emission is not None:
            try:
                bsdf.inputs['Emission Color'].default_value = (emission[0], emission[1], emission[2], 1.0)
                bsdf.inputs['Emission Strength'].default_value = emission_strength
            except Exception:
                pass
    except Exception:
        pass
    return mat


# ---------------------------------------------------------------- geometry helpers
def lathe(profile, segments=SEG, closed=False, sharp_angle=35.0):
    """Revolve a list of (radius, z) points around the local Z axis.
    radius <= 0 gives a pole vertex. closed=True links last ring back to first."""
    bm = bmesh.new()
    rings = []
    for (r, z) in profile:
        if r <= 1e-6:
            rings.append([bm.verts.new((0.0, 0.0, z))])
        else:
            ring = []
            for i in range(segments):
                a = 2.0 * math.pi * i / segments
                ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), z)))
            rings.append(ring)
    n = len(rings)
    pairs = [(k, k + 1) for k in range(n - 1)]
    if closed:
        pairs.append((n - 1, 0))
    for (ka, kb) in pairs:
        A, B = rings[ka], rings[kb]
        if len(A) == 1 and len(B) == 1:
            continue
        for i in range(segments):
            i2 = (i + 1) % segments
            if len(A) == 1:
                bm.faces.new((A[0], B[i], B[i2]))
            elif len(B) == 1:
                bm.faces.new((A[i], A[i2], B[0]))
            else:
                bm.faces.new((A[i], A[i2], B[i2], B[i]))
    bm.normal_update()
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.normal_update()
    thr = math.radians(sharp_angle)
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        ang = e.calc_face_angle(0.0)
        e.smooth = ang < thr
    return bm


def sphere_profile(r, cz, n=18):
    pts = []
    for j in range(n + 1):
        phi = math.pi * j / n
        pts.append((r * math.sin(phi), cz - r * math.cos(phi)))
    return pts


def cylinder_profile(r, z0, z1):
    return [(0.0, z0), (r, z0), (r, z1), (0.0, z1)]


def add_object(name, bm, mat, matrix=None):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if matrix is not None:
        obj.matrix_world = matrix
    mesh.materials.append(mat)
    return obj


# ---------------------------------------------------------------- build
def main():
    mat_dark = make_material("LampDarkMetal", (0.07, 0.07, 0.08), 0.35, 1.0)
    mat_steel = make_material("LampBrushedSteel", (0.72, 0.72, 0.74), 0.45, 1.0)
    mat_plastic = make_material("LampBlackPlastic", (0.02, 0.02, 0.02), 0.6, 0.0)
    mat_bulb = make_material("LampBulb", (1.0, 0.93, 0.8), 0.3, 0.0,
                             emission=(1.0, 0.9, 0.7), emission_strength=3.0)

    # ---- heavy round base on the ground (z = 0), centred on Z axis
    R, H, b = 0.095, 0.032, 0.009
    prof = [(0.0, 0.0), (R, 0.0), (R, H - b)]
    for k in range(1, 6):
        ang = (math.pi / 2.0) * k / 5.0
        prof.append((R - b + b * math.cos(ang), H - b + b * math.sin(ang)))
    prof.append((0.0, H))
    add_object("Base", lathe(prof, SEG), mat_dark)

    # ---- collar on top of the base where the arm is mounted
    prof = [(0.0, H - 0.002), (0.021, H - 0.002), (0.021, H + 0.020),
            (0.017, H + 0.024), (0.0, H + 0.024)]
    add_object("BaseCollar", lathe(prof, 48), mat_dark)

    # ---- arm joint (ball) on the collar
    P0 = Vector((0.0, 0.0, 0.060))
    add_object("ArmJoint", lathe(sphere_profile(0.019, 0.0), 32), mat_dark,
               Matrix.Translation(P0))

    # ---- single straight arm leaning forward (toward -Y)
    tilt = math.radians(30.0)
    L = 0.42
    d = Vector((0.0, -math.sin(tilt), math.cos(tilt)))
    arm_mat = Matrix.Translation(P0) @ Matrix.Rotation(tilt, 4, 'X')
    add_object("Arm", lathe(cylinder_profile(0.009, -0.005, L), 32), mat_dark, arm_mat)
    P1 = P0 + d * L

    # ---- joint ball at the top of the arm
    add_object("ShadeJoint", lathe(sphere_profile(0.017, 0.0), 32), mat_dark,
               Matrix.Translation(P1))

    # ---- shade frame: axis points up & slightly back, so the opening faces down/forward
    a = math.radians(12.0)
    axis = Vector((0.0, math.sin(a), math.cos(a)))
    Rs, rt, h, t = 0.10, 0.03, 0.14, 0.003
    neck_top = h + 0.032
    B = P1 - axis * neck_top
    shade_mat = Matrix.Translation(B) @ Matrix.Rotation(-a, 4, 'X')

    # conical metal shade, hollow shell with thickness, opening at local z=0 (downward)
    prof = [(Rs, 0.0), (rt, h), (rt - t, h), (Rs - t, 0.0)]
    add_object("Shade", lathe(prof, SEG, closed=True), mat_steel, shade_mat)

    # cap closing the top of the cone
    prof = [(0.0, h - 0.004), (0.034, h - 0.004), (0.033, h + 0.010),
            (0.028, h + 0.014), (0.0, h + 0.014)]
    add_object("ShadeCap", lathe(prof, 48), mat_steel, shade_mat)

    # neck between cap and the arm joint
    add_object("ShadeNeck", lathe(cylinder_profile(0.011, h + 0.005, neck_top), 32),
               mat_steel, shade_mat)

    # bulb socket inside the shade
    add_object("Socket", lathe(cylinder_profile(0.013, h - 0.045, h - 0.002), 32),
               mat_plastic, shade_mat)

    # bulb
    add_object("Bulb", lathe(sphere_profile(0.026, h - 0.067), 32), mat_bulb, shade_mat)

    # ---- small switch on the base
    prof = [(0.0, 0.0), (0.007, 0.0), (0.007, 0.006), (0.005, 0.008), (0.0, 0.008)]
    add_object("Switch", lathe(prof, 32), mat_plastic,
               Matrix.Translation(Vector((0.05, -0.045, H - 0.001))))


main()
```