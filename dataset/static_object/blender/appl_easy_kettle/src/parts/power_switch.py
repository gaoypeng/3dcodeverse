"""PowerSwitch — rocker toggle power lever at the base of the handle (part module; imported by src/model.py).

Small translucent rocker switch toggle (14 × 18 × 12 mm) positioned below the lower handle mount with an embedded LED indicator zone.
Material: semi-translucent frosted polycarbonate.  Instances: 1.  Attaches to: Handle (must touch, no gap).

Exports `build_power_switch() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

# Plan numbers
# center (0.000, 0.076, 0.048) extents (0.016, 0.018, 0.014)
# x in [-0.008, 0.008]  y in [0.067, 0.085]  z in [0.041, 0.055]
POWER_SWITCH_CENTER = (0.000, 0.076, 0.048)
POWER_SWITCH_EXTENTS = (0.016, 0.018, 0.014)


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_power_switch() -> bpy.types.Object:
    bm = bmesh.new()

    # Small angled rocker paddle switch
    # Extents: 16 mm wide (X), 18 mm deep (Y), 14 mm high (Z)
    # x: [-0.008, 0.008]
    # y: [0.067, 0.085]
    # z: [0.041, 0.055]
    
    # We construct a wedge-shaped toggle switch with chamfered top/edges:
    bmesh.ops.create_cube(bm, size=1.0)
    # Scale to match extents
    bmesh.ops.scale(bm, vec=POWER_SWITCH_EXTENTS, verts=bm.verts)
    
    # Slightly taper/tilt the toggle lever forward/down
    for v in bm.verts:
        # if at outer end (+Y), lower slightly to look like a rocker
        if v.co.y > 0.002:
            v.co.z -= 0.002
    
    # Translate to center
    bmesh.ops.translate(bm, vec=POWER_SWITCH_CENTER, verts=bm.verts)

    me = bpy.data.meshes.new("PowerSwitch")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("PowerSwitch", me)
    bpy.context.scene.collection.objects.link(obj)

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.001
    bev.segments = 2
    bev.limit_method = "ANGLE"

    # Frosted semi-translucent polycarbonate with subtle amber/blue LED tint
    mat = make_material("FrostedPolycarbonate", (0.82, 0.88, 0.92), roughness=0.2, metallic=0.1)
    obj.data.materials.append(mat)

    return obj
