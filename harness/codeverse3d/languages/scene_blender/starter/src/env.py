"""src/env.py — the environment of the EXAMPLE scene (a lakeside hamlet): a low warm sun, rolling
meadow beyond a level pad, a lake basin, haze.  The landform is this example's, not a rule.

Frame: Z up, -Y front, metres.
CONTRACT: height_at(x, y) -> z is THE ground: the terrain mesh and every zone's seat use it.
  build_env(ctx) -> dict builds the world, lamps, ground and atmosphere into ctx.collection and
  returns what zones may share (materials, water level, sun direction ...) — they read it as
  ctx.env.  Code the zones share (`material`, `linked`, `seat`) lives here: `from env import ...`.
"""
import math

import bmesh
import bpy

GROUND_SIZE = 160.0
FLAT_RADIUS = 44.0          # THIS example's landform: level under its hamlet, hills beyond — one landform among many; yours is the brief's
SUN_AZIMUTH_DEG = 215.0     # compass angle of the sun: 0 = +X, 90 = +Y (the sky and the lamp share it)
SUN_ELEVATION_DEG = 22.0
BASIN = (-24.0, 2.0, 15.0, 1.2)   # the lake's (x, y, radius, depth): zones/lakeside.py fills it to WATER_LEVEL
WATER_LEVEL = -0.3


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def height_at(x, y):
    """z of the ground at (x, y): level inside FLAT_RADIUS, rolling hills beyond, minus a smooth-rimmed bowl under the lake."""
    hills = 1.2 * math.sin(x / 23.0) * math.cos(y / 19.0) + 0.8 * math.sin((x + y) / 31.0)
    rise = _smooth((math.hypot(x, y) - FLAT_RADIUS) / 30.0)
    bx, by, br, bd = BASIN
    bowl = bd * (1.0 - _smooth((math.hypot(x - bx, y - by) / br - 0.3) / 0.7))   # a wide rim: banks under 15 % so props seat on them
    return rise * (hills + 2.5 * rise) - bowl


def material(name, rgb, roughness=0.8, noise_scale=3.0, darker=0.6):
    """A Principled material varied at two scales: a noise mixes `rgb` with a darker tone over metres and bumps it over centimetres."""
    mat = bpy.data.materials.new(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = roughness
    tone = nodes.new("ShaderNodeTexNoise")
    tone.inputs["Scale"].default_value = noise_scale * 0.1
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[6].default_value = (*rgb, 1.0)
    mix.inputs[7].default_value = (*(c * darker for c in rgb), 1.0)
    links.new(tone.outputs["Fac"], mix.inputs["Factor"])
    links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = noise_scale * 12.0
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def linked(ctx, name, bm, *mats):
    """`bm` as a mesh object named `name` with material slots `mats`, linked into ctx.collection (`bm` is freed)."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for mat in mats:
        me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ctx.collection.objects.link(ob)
    return ob


def seat(ob, height_at, sink=0.0):
    """Rest a root built with its base on local z = 0 on the ground: the lowest ground under its footprint corners, minus `sink`."""
    r = max(ob.dimensions.x, ob.dimensions.y) / 2.0 if ob.type == "MESH" else 0.5
    x, y = ob.location.x, ob.location.y
    ob.location.z = min(height_at(x + dx, y + dy) for dx in (-r, 0.0, r) for dy in (-r, 0.0, r)) - sink
    return ob


def build_env(ctx):
    world = bpy.data.worlds.new("GoldenHour")
    ctx.scene.world = world
    nodes, links = world.node_tree.nodes, world.node_tree.links
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_disc = False                        # the Sun lamp below casts the shadows: one sun, not two
    sky.sun_elevation = math.radians(SUN_ELEVATION_DEG)
    sky.sun_rotation = math.radians(SUN_AZIMUTH_DEG)
    sky.aerosol_density = 2.5
    nodes["Background"].inputs["Strength"].default_value = 0.35
    links.new(sky.outputs["Color"], nodes["Background"].inputs["Color"])

    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy, sun.color, sun.angle = 3.5, (1.0, 0.79, 0.54), math.radians(1.5)
    lamp = bpy.data.objects.new("Sun", sun)      # a Sun shines down its own -Z
    lamp.rotation_euler = (math.radians(90.0 - SUN_ELEVATION_DEG), 0.0, math.radians(SUN_AZIMUTH_DEG + 90.0))
    ctx.collection.objects.link(lamp)
    el, az = math.radians(SUN_ELEVATION_DEG), math.radians(SUN_AZIMUTH_DEG)
    towards_sun = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))

    ground_mat = material("GroundMat", (0.31, 0.37, 0.22), roughness=0.92, noise_scale=5.0)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=112, y_segments=112, size=GROUND_SIZE / 2)   # size is the half-side
    for v in bm.verts:
        v.co.z = height_at(v.co.x, v.co.y)
    linked(ctx, "Ground", bm, ground_mat)

    haze = bpy.data.materials.new("Haze")        # volume-only: the atmosphere the probe requires
    nodes = haze.node_tree.nodes
    nodes.remove(nodes["Principled BSDF"])
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 0.0025
    vol.inputs["Color"].default_value = (0.85, 0.78, 0.66, 1.0)
    haze.node_tree.links.new(vol.outputs["Volume"], nodes["Material Output"].inputs["Volume"])
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(GROUND_SIZE, GROUND_SIZE, 24.0), verts=bm.verts)
    linked(ctx, "Atmosphere", bm, haze).location.z = 12.0
    return {"ground_material": ground_mat, "sun_direction": towards_sun, "water_level": WATER_LEVEL}
