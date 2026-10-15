"""lib/sky.py — the world (sky), fog volumes and sky dressing.  ENV ONLY for the world setters:
``scene.world`` belongs to ``src/env.py``'s ``build_env`` (the lint rejects it in a zone).

The census reads the world's background kind (``sky:MULTIPLE_SCATTERING`` / a hex colour / a
node) and the fog from either the world's Volume output or the largest volume-only box
(``haze``); a scene with neither reads as thin_atmosphere.  Blender 5.2 facts: the physical
sky is ``MULTIPLE_SCATTERING`` (the old name raises), its dust knob is ``aerosol_density``, and
the sun disc is ON by default — ``sun_world`` turns it off because ``lighting.sun`` is the sun.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import bpy

from lib.materials import rgba
from lib.mesh import mesh_object
from lib.noise import fbm2, rng


def _rgb(color: str | Sequence[float]) -> tuple[float, float, float, float]:
    if isinstance(color, str):
        return rgba(color)
    v = [float(c) for c in color]
    return (v[0], v[1], v[2], v[3] if len(v) > 3 else 1.0)


def _fresh_world(scene: Any, name: str) -> tuple[Any, Any, Any, Any]:
    world = bpy.data.worlds.new(name)
    scene.world = world
    nt = world.node_tree
    bg = nt.nodes.get("Background") or nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.get("World Output") or nt.nodes.new("ShaderNodeOutputWorld")
    if not out.inputs["Surface"].is_linked:
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return world, nt.nodes, nt.links, bg


def sun_world(scene: Any, *, elevation_deg: float = 12.0, azimuth_deg: float = 125.0, strength: float = 0.2,
              dust: float = 2.0) -> Any:
    """A physical sky (MULTIPLE_SCATTERING) lit from compass ``azimuth_deg`` (0 = +X, 90 = +Y —
    the same numbers as ``lighting.sun``, so the sky's bright side and the shadows agree) at
    ``elevation_deg``; ``strength`` is the world's Background strength, ``dust`` the aerosol
    density (1 clear, 2-4 hazy).  The defaults are a hazy low afternoon — pass the brief's time
    of day and weather; strength and dust together decide where a day frame lands in the judged
    luminance band.  The sun disc is off: the Sun lamp casts the shadows."""
    world, nodes, links, bg = _fresh_world(scene, "Sky")
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_disc = False
    sky.sun_elevation = math.radians(elevation_deg)
    sky.sun_rotation = math.radians(azimuth_deg - 90.0)   # Blender: rotation 0 puts the sun at +Y
    sky.aerosol_density = float(dust)
    links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = float(strength)
    return world


def night_world(scene: Any, color: str | Sequence[float] = "17273F", strength: float = 0.3) -> Any:
    """A flat night world in ``color``.  A black world fails the judged luminance floor (AgX has
    nothing to lift); give it the brief's colour with some value — deep blue, sodium amber,
    moonlit grey."""
    world, _, _, bg = _fresh_world(scene, "NightSky")
    bg.inputs["Color"].default_value = _rgb(color)
    bg.inputs["Strength"].default_value = float(strength)
    return world


def gradient_world(scene: Any, stops: Sequence[tuple[float, str | Sequence[float]]], *, clouds: bool = True,
                   strength: float = 0.7) -> Any:
    """A sky graded from the horizon (position 0) to the zenith (1) through ``stops`` =
    ``[(0.0, "F2C9A0"), (0.35, "7FA7D6"), (1.0, "1F4A8E")]``; ``clouds`` mottles the upper half
    with noise.  For dusk / dawn / stylised skies where the physical sky is wrong."""
    world, nodes, links, bg = _fresh_world(scene, "GradientSky")
    tc = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    ordered = sorted(((float(p), _rgb(c)) for p, c in stops), key=lambda s: s[0])
    els[0].position, els[0].color = max(0.0, ordered[0][0]), ordered[0][1]
    els[1].position, els[1].color = min(1.0, ordered[-1][0]), ordered[-1][1]
    for pos, col in ordered[1:-1]:
        e = els.new(max(0.0, min(1.0, pos)))
        e.color = col
    links.new(sep.outputs["Z"], ramp.inputs["Fac"])   # Generated z = 0 at the horizon, 1 at the zenith
    colour = ramp.outputs["Color"]
    if clouds:
        n = nodes.new("ShaderNodeTexNoise")
        n.inputs["Scale"].default_value = 2.5
        n.inputs["Detail"].default_value = 6.0
        n.inputs["Roughness"].default_value = 0.6
        links.new(tc.outputs["Generated"], n.inputs["Vector"])
        mask = nodes.new("ShaderNodeValToRGB")
        mask.color_ramp.elements[0].position = 0.55
        mask.color_ramp.elements[1].position = 0.75
        links.new(n.outputs["Fac"], mask.inputs["Fac"])
        math_node = nodes.new("ShaderNodeMath")
        math_node.operation = "MULTIPLY"
        links.new(mask.outputs["Color"], math_node.inputs[0])
        links.new(sep.outputs["Z"], math_node.inputs[1])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs[7].default_value = (0.9, 0.9, 0.92, 1.0)
        links.new(colour, mix.inputs[6])
        links.new(math_node.outputs["Value"], mix.inputs[0])
        colour = mix.outputs[2]
    links.new(colour, bg.inputs["Color"])
    bg.inputs["Strength"].default_value = float(strength)
    return world


def haze(collection: Any, loc: Sequence[float], size: float | Sequence[float], density: float = 0.003,
         color: str | Sequence[float] = "ADC9E1", name: str = "Atmosphere") -> Any:
    """A bounded fog volume: a box centred at ``loc`` of ``size`` m (a number, or (x, y, z))
    whose only material is a Principled Volume (``density`` per metre: 0.002 clear valley air,
    0.01 morning mist, 0.03 a dense fog bank).  Volume-only, so the census reports it as fog,
    not matter.  A box rather than a world volume because a world volume over a large scene
    swallows the sun (whether the sun still reaches the frame shows in the luminance band);
    size and place it for the brief — a valley floor, a riverbank, the whole site."""
    sx, sy, sz = (size, size, size) if isinstance(size, (int, float)) else size
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    cx, cy, cz = (float(v) for v in loc)
    verts = [(cx + x, cy + y, cz + z) for x, y, z in ((-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
                                                       (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz))]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mat = bpy.data.materials.new(f"{name}Fog")
    nt = mat.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    fog = nt.nodes.new("ShaderNodeVolumePrincipled")
    fog.inputs["Density"].default_value = float(density)
    fog.inputs["Color"].default_value = _rgb(color)
    fog.inputs["Anisotropy"].default_value = 0.3
    nt.links.new(fog.outputs["Volume"], nt.nodes["Material Output"].inputs["Volume"])
    return mesh_object(name, verts, faces, mat, collection)


def aurora(name: str, collection: Any, center: Sequence[float], *, span: float = 560.0, height: float = 72.0, seed: int = 0,
           strength: float = 0.24, ctx: Any = None) -> Any:
    """A curtain of aurora: a wavy emissive ribbon ``span`` m long and ``height`` m tall hung at
    ``center`` (x, y, altitude).  Tagged ``placement = "free"`` (it is in the sky); with ``ctx``
    the glow drifts along the ribbon between ``ctx.times``."""
    n = 48
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    cx, cy, cz = (float(v) for v in center)
    for i in range(n + 1):
        t = i / n
        x = cx - span / 2 + span * t
        y = cy + 18.0 * fbm2(t * 3.0, seed * 0.37, octaves=3, seed=seed)
        lift = 6.0 * fbm2(t * 5.0 + 11.0, seed * 0.11, octaves=2, seed=seed + 1)
        verts.append((x, y, cz + lift))
        verts.append((x, y, cz + height + lift * 1.5))
    for i in range(n):
        a = 2 * i
        faces.append((a, a + 2, a + 3, a + 1))
    mat = bpy.data.materials.new(f"{name}Glow")
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes["Principled BSDF"]
    nodes.remove(bsdf)
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = float(strength) * 8.0
    tc = nodes.new("ShaderNodeTexCoord")
    mp = nodes.new("ShaderNodeMapping")
    mp.name = "FlowMapping"
    mp.inputs["Scale"].default_value = (0.02, 0.02, 0.05)
    links.new(tc.outputs["Object"], mp.inputs["Vector"])
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.0
    noise.inputs["Detail"].default_value = 4.0
    links.new(mp.outputs["Vector"], noise.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.35, (0.05, 0.9, 0.45, 1.0)
    els[1].position, els[1].color = 0.75, (0.5, 0.2, 0.9, 1.0)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], emit.inputs["Color"])
    # fade to nothing along the bottom edge and where the noise is dark
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    fade = nodes.new("ShaderNodeMath")
    fade.operation = "MULTIPLY"
    links.new(sep.outputs["Z"], fade.inputs[0])
    links.new(noise.outputs["Fac"], fade.inputs[1])
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(fade.outputs["Value"], mix.inputs["Fac"])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emit.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], nodes["Material Output"].inputs["Surface"])
    mat.surface_render_method = "BLENDED"
    ob = mesh_object(name, verts, faces, mat, collection)
    ob["placement"] = "free"
    if ctx is not None:
        from lib.motion import scroll

        scroll(mp, (0.6, 0.0, 0.0), ctx)
    return ob


def stars(batch: Any, mat: Any, *, seed: int = 0, count: int = 900, radius: float = 600.0) -> None:
    """``count`` star quads on a dome of ``radius`` m above the horizon, added to ``batch`` with
    an ``emissive`` ``mat``.  Name the batch ``Stars…`` (sky-named and 1 km wide, so the census
    files it as sky) and tag its root ``["placement"] = "free"``."""
    r = rng(seed)
    for _ in range(max(0, int(count))):
        az = r.uniform(0.0, math.tau)
        el = math.asin(r.uniform(0.08, 1.0))
        size = r.uniform(0.6, 1.8)
        cx, cy, cz = radius * math.cos(el) * math.cos(az), radius * math.cos(el) * math.sin(az), radius * math.sin(el)
        # a quad facing the origin: span two directions orthogonal to the radial line
        ux, uy = -math.sin(az), math.cos(az)
        vx, vy, vz = -math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el)
        verts = [(cx + (ux * a + vx * b) * size, cy + (uy * a + vy * b) * size, cz + vz * b * size)
                 for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        batch.add(mat, verts, [(0, 1, 2, 3)])
