"""lib/materials.py — Principled materials without texture files (Blender 5.2 socket names).

Colours are sRGB hex strings (``"8A6A3F"``) converted to linear here, or an already-linear
(r, g, b) tuple.  Procedural detail reads OBJECT texture coordinates, so a pattern keeps its
metre scale whatever the object's size.  Under Cycles + AgX at exposure 0 a non-emissive
albedo belongs in 0.02-0.8 linear: pure black or pure white reads as a hole or a blown
surface.  Never ``use_nodes`` (deprecated in 5.x: every material has a node tree).
"""

from __future__ import annotations

import colorsys
from collections.abc import Sequence
from typing import Any

import bpy

RGBA = tuple[float, float, float, float]


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgba(hex_color: str, alpha: float = 1.0) -> RGBA:
    """``"RRGGBB"`` (sRGB, optional ``#``) → linear (r, g, b, alpha) for a colour socket."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b), float(alpha))


def _rgb(color: str | Sequence[float]) -> RGBA:
    """A hex string or a linear (r, g, b[, a]) tuple as RGBA."""
    if isinstance(color, str):
        return rgba(color)
    vals = [float(v) for v in color]
    return (vals[0], vals[1], vals[2], vals[3] if len(vals) > 3 else 1.0)


def _scaled(c: RGBA, k: float) -> RGBA:
    return (min(1.0, c[0] * k), min(1.0, c[1] * k), min(1.0, c[2] * k), c[3])


def _tree(mat: Any) -> tuple[Any, Any, Any, Any]:
    nt = mat.node_tree
    return nt, nt.nodes, nt.links, nt.nodes["Principled BSDF"]


def _coords(nodes: Any, links: Any, scale: float = 1.0) -> Any:
    """Object-space coordinates scaled by ``scale`` (features per metre): the vector socket."""
    tc = nodes.new("ShaderNodeTexCoord")
    mp = nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(tc.outputs["Object"], mp.inputs["Vector"])
    return mp.outputs["Vector"]


def _noise(nodes: Any, links: Any, vector: Any, scale: float = 1.0, detail: float = 4.0, roughness: float = 0.5) -> Any:
    n = nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = float(scale)
    n.inputs["Detail"].default_value = float(detail)
    n.inputs["Roughness"].default_value = float(roughness)
    if vector is not None:
        links.new(vector, n.inputs["Vector"])
    return n


def _mix(nodes: Any, links: Any, a: RGBA, b: RGBA, factor: Any) -> Any:
    """``ShaderNodeMix`` (RGBA) between ``a`` and ``b`` driven by ``factor``: the colour output."""
    m = nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.inputs[6].default_value = a
    m.inputs[7].default_value = b
    links.new(factor, m.inputs[0])
    return m.outputs[2]


def _bump(nodes: Any, links: Any, height: Any, bsdf: Any, strength: float = 0.1, distance: float = 0.05) -> None:
    b = nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = float(strength)
    b.inputs["Distance"].default_value = float(distance)
    links.new(height, b.inputs["Height"])
    links.new(b.outputs["Normal"], bsdf.inputs["Normal"])


def _ramp(nodes: Any, links: Any, factor: Any, stops: Sequence[tuple[float, RGBA]], interpolation: str = "LINEAR") -> Any:
    r = nodes.new("ShaderNodeValToRGB")
    r.color_ramp.interpolation = interpolation
    els = r.color_ramp.elements
    els[0].position, els[0].color = stops[0]
    els[1].position, els[1].color = stops[-1]
    for pos, col in stops[1:-1]:
        e = els.new(pos)
        e.color = col
    links.new(factor, r.inputs["Fac"])
    return r.outputs["Color"]


def material(name: str, color: str | Sequence[float], roughness: float = 0.5, metal: float = 0.0, *,
             noise_scale: float = 0.0, bump: float = 0.08, emission: float = 0.0) -> Any:
    """A Principled material.  ``noise_scale`` > 0 (features per metre) adds a subtle colour
    breakup and a bump of ``bump`` strength; ``emission`` > 0 makes it glow in its own colour."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    col = _rgb(color)
    bsdf.inputs["Base Color"].default_value = col
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Metallic"].default_value = float(metal)
    if emission > 0:
        bsdf.inputs["Emission Color"].default_value = col
        bsdf.inputs["Emission Strength"].default_value = float(emission)
    if noise_scale > 0:
        vec = _coords(nodes, links, 1.0)
        n = _noise(nodes, links, vec, scale=noise_scale, detail=5.0, roughness=0.55)
        links.new(_mix(nodes, links, _scaled(col, 0.82), _scaled(col, 1.12), n.outputs["Fac"]), bsdf.inputs["Base Color"])
        _bump(nodes, links, n.outputs["Fac"], bsdf, strength=bump, distance=0.04)
    return mat


def masonry(name: str, color: str | Sequence[float], mortar: str = "625D66", scale: float = 2.0) -> Any:
    """Bricks in ``color`` with ``mortar`` joints, ``scale`` rows per metre (Brick texture on
    object coordinates, so a wall of any size keeps brick-sized bricks)."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    col = _rgb(color)
    br = nodes.new("ShaderNodeTexBrick")
    br.inputs["Color1"].default_value = _scaled(col, 0.9)
    br.inputs["Color2"].default_value = _scaled(col, 1.1)
    br.inputs["Mortar"].default_value = _rgb(mortar)
    br.inputs["Scale"].default_value = float(scale)
    br.inputs["Mortar Size"].default_value = 0.025
    br.inputs["Mortar Smooth"].default_value = 0.1
    links.new(_coords(nodes, links, 1.0), br.inputs["Vector"])
    links.new(br.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.85
    _bump(nodes, links, br.outputs["Fac"], bsdf, strength=0.35, distance=0.02)
    return mat


def limestone(name: str, color: str | Sequence[float], scale: float = 1.2) -> Any:
    """Pale blotchy stone: noise colour breakup + a fine grain bump (``scale`` blotches / m)."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    col = _rgb(color)
    vec = _coords(nodes, links, 1.0)
    blotch = _noise(nodes, links, vec, scale=scale, detail=3.0, roughness=0.6)
    grain = _noise(nodes, links, vec, scale=scale * 18.0, detail=6.0, roughness=0.7)
    links.new(_mix(nodes, links, _scaled(col, 0.78), _scaled(col, 1.08), blotch.outputs["Fac"]), bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.8
    _bump(nodes, links, grain.outputs["Fac"], bsdf, strength=0.12, distance=0.01)
    return mat


def ashlar(name: str, color: str | Sequence[float]) -> Any:
    """Dressed stone blocks 1.2 x 0.4 m with thin joints."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    col = _rgb(color)
    br = nodes.new("ShaderNodeTexBrick")
    br.inputs["Color1"].default_value = _scaled(col, 0.92)
    br.inputs["Color2"].default_value = _scaled(col, 1.06)
    br.inputs["Mortar"].default_value = _scaled(col, 0.45)
    br.inputs["Scale"].default_value = 1.0
    br.inputs["Mortar Size"].default_value = 0.012
    br.inputs["Brick Width"].default_value = 1.2
    br.inputs["Row Height"].default_value = 0.4
    links.new(_coords(nodes, links, 1.0), br.inputs["Vector"])
    links.new(br.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.75
    _bump(nodes, links, br.outputs["Fac"], bsdf, strength=0.3, distance=0.015)
    return mat


def woodgrain(mat: Any, dark: str = "29190F", light: str = "805939", direction: Sequence[float] = (3, 0.1, 5)) -> Any:
    """Give ``mat`` a stretched-noise grain between ``dark`` and ``light``; ``direction`` scales
    the noise per axis (long along the grain, short across).  Returns ``mat``."""
    _, nodes, links, bsdf = _tree(mat)
    tc = nodes.new("ShaderNodeTexCoord")
    mp = nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = tuple(float(v) for v in direction)
    links.new(tc.outputs["Object"], mp.inputs["Vector"])
    n = _noise(nodes, links, mp.outputs["Vector"], scale=4.0, detail=6.0, roughness=0.6)
    ramp = _ramp(nodes, links, n.outputs["Fac"], [(0.35, _rgb(dark)), (0.65, _rgb(light))])
    links.new(ramp, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.15, distance=0.01)
    return mat


def chipped_paint(mat: Any, paint: str | Sequence[float], substrate: str | Sequence[float], scale: float = 6.0) -> Any:
    """Paint in ``paint`` worn through to ``substrate`` in noisy patches (``scale`` patches / m)."""
    _, nodes, links, bsdf = _tree(mat)
    vec = _coords(nodes, links, 1.0)
    n = _noise(nodes, links, vec, scale=scale, detail=4.0, roughness=0.65)
    mask = _ramp(nodes, links, n.outputs["Fac"], [(0.42, (0, 0, 0, 1)), (0.5, (1, 1, 1, 1))])
    m = nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.inputs[6].default_value = _rgb(substrate)
    m.inputs[7].default_value = _rgb(paint)
    links.new(mask, m.inputs[0])
    links.new(m.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.5
    _bump(nodes, links, mask, bsdf, strength=0.25, distance=0.003)
    return mat


def worn_alloy(name: str, color: str | Sequence[float], *, seed: int = 0, metal: float = 0.55) -> Any:
    """Dull metal with streaky roughness; ``seed`` shifts the streak pattern."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    bsdf.inputs["Base Color"].default_value = _rgb(color)
    bsdf.inputs["Metallic"].default_value = float(metal)
    tc = nodes.new("ShaderNodeTexCoord")
    mp = nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1.0, 14.0, 1.0)
    mp.inputs["Location"].default_value = (seed * 3.7, seed * 1.3, 0.0)
    links.new(tc.outputs["Object"], mp.inputs["Vector"])
    n = _noise(nodes, links, mp.outputs["Vector"], scale=3.0, detail=5.0, roughness=0.6)
    rough = _ramp(nodes, links, n.outputs["Fac"], [(0.3, (0.25, 0.25, 0.25, 1)), (0.7, (0.7, 0.7, 0.7, 1))])
    links.new(rough, bsdf.inputs["Roughness"])
    _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.05, distance=0.003)
    return mat


def mineral_surface(name: str, color: str | Sequence[float], *, stone: bool = False) -> Any:
    """Rock / soil in ``color``: mottled by noise; ``stone=True`` adds Voronoi crack lines."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    col = _rgb(color)
    vec = _coords(nodes, links, 1.0)
    n = _noise(nodes, links, vec, scale=2.5, detail=6.0, roughness=0.6)
    base = _mix(nodes, links, _scaled(col, 0.7), _scaled(col, 1.15), n.outputs["Fac"])
    if stone:
        vor = nodes.new("ShaderNodeTexVoronoi")
        vor.inputs["Scale"].default_value = 1.6
        links.new(vec, vor.inputs["Vector"])
        cracks = _ramp(nodes, links, vor.outputs["Distance"], [(0.0, (0.3, 0.3, 0.3, 1)), (0.08, (1, 1, 1, 1))])
        m = nodes.new("ShaderNodeMix")
        m.data_type = "RGBA"
        m.blend_type = "MULTIPLY"
        m.inputs[0].default_value = 1.0
        links.new(base, m.inputs[6])
        links.new(cracks, m.inputs[7])
        base = m.outputs[2]
        _bump(nodes, links, vor.outputs["Distance"], bsdf, strength=0.4, distance=0.02)
    else:
        _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.2, distance=0.03)
    links.new(base, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.9
    return mat


def water(name: str, color: str = "163F4A", roughness: float = 0.12, scale: float = 0.8, *,
          flow: Sequence[float] = (0.6, 0.2), ctx: Any = None) -> Any:
    """A refracting water surface tinted ``color`` with rippled normals (``scale`` ripples / m);
    with ``ctx`` the ripples drift ``flow`` m/s between ``ctx.times`` (keyframes on the mapping)."""
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    bsdf.inputs["Base Color"].default_value = _rgb(color)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Transmission Weight"].default_value = 0.75
    bsdf.inputs["IOR"].default_value = 1.33
    bsdf.inputs["Coat Weight"].default_value = 0.3
    tc = nodes.new("ShaderNodeTexCoord")
    mp = nodes.new("ShaderNodeMapping")
    mp.name = "FlowMapping"
    links.new(tc.outputs["Object"], mp.inputs["Vector"])
    n = _noise(nodes, links, mp.outputs["Vector"], scale=scale, detail=6.0, roughness=0.55)
    _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.08, distance=0.05)
    if ctx is not None:
        from lib.motion import scroll

        scroll(mp, (float(flow[0]), float(flow[1]), 0.0), ctx)
    return mat


def clear_water(name: str) -> Any:
    """Shallow, almost colourless water (a basin, a fountain bowl, a stream)."""
    mat = water(name, color="9CC8CF", roughness=0.05, scale=1.4)
    mat.node_tree.nodes["Principled BSDF"].inputs["Transmission Weight"].default_value = 0.95
    return mat


def glass(name: str, color: str = "FFFFFF", roughness: float = 0.05, ior: float = 1.5) -> Any:
    mat = bpy.data.materials.new(name)
    _, _, _, bsdf = _tree(mat)
    bsdf.inputs["Base Color"].default_value = _rgb(color)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = float(ior)
    return mat


def foliage(name: str, color: str | Sequence[float], variants: int = 3) -> list[Any]:
    """``variants`` leaf materials around ``color`` (hue and lightness spread, the spread a
    photograph carries): give neighbouring trees different ones."""
    base = _rgb(color)
    h, l_, s = colorsys.rgb_to_hls(*base[:3])
    out = []
    n = max(1, int(variants))
    for i in range(n):
        t = (i / (n - 1) - 0.5) if n > 1 else 0.0
        r, g, b = colorsys.hls_to_rgb((h + 0.045 * t) % 1.0, max(0.02, min(0.8, l_ * (1.0 + 0.35 * t))), max(0.0, min(1.0, s * (1.0 + 0.2 * t))))
        mat = bpy.data.materials.new(f"{name}_{i}")
        _, nodes, links, bsdf = _tree(mat)
        bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
        bsdf.inputs["Specular IOR Level"].default_value = 0.3
        n_ = _noise(nodes, links, _coords(nodes, links, 1.0), scale=30.0, detail=2.0, roughness=0.5)
        links.new(_mix(nodes, links, (r * 0.75, g * 0.75, b * 0.75, 1.0), (min(1.0, r * 1.2), min(1.0, g * 1.2), min(1.0, b * 1.2), 1.0),
                       n_.outputs["Fac"]), bsdf.inputs["Base Color"])
        out.append(mat)
    return out


def snow(name: str = "Snow") -> Any:
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    bsdf.inputs["Base Color"].default_value = (0.86, 0.88, 0.92, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.55
    bsdf.inputs["Subsurface Weight"].default_value = 0.2
    bsdf.inputs["Subsurface Radius"].default_value = (0.3, 0.3, 0.35)
    n = _noise(nodes, links, _coords(nodes, links, 1.0), scale=25.0, detail=5.0, roughness=0.6)
    _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.1, distance=0.01)
    return mat


def ice(name: str = "Ice") -> Any:
    mat = bpy.data.materials.new(name)
    _, nodes, links, bsdf = _tree(mat)
    bsdf.inputs["Base Color"].default_value = (0.8, 0.9, 0.97, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.18
    bsdf.inputs["Transmission Weight"].default_value = 0.8
    bsdf.inputs["IOR"].default_value = 1.31
    n = _noise(nodes, links, _coords(nodes, links, 1.0), scale=3.0, detail=7.0, roughness=0.7)
    _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.3, distance=0.02)
    return mat


def emissive(name: str, color: str | Sequence[float], strength: float) -> Any:
    """A light-giving skin (a lantern paper, a sign, a window pane): ``strength`` 3-8 READS as a lit
    fixture, 20+ as sun-bright — but it does not light a frame: measured on Blender 5.2.2 two
    fixture-sized emitters at strength 5-80 left a night frame at mean luminance 0.03-0.05
    (dark_frame).  Pair every emissive fixture with ``lighting.practical`` for the light itself."""
    mat = bpy.data.materials.new(name)
    _, _, _, bsdf = _tree(mat)
    col = _rgb(color)
    bsdf.inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1.0)
    bsdf.inputs["Emission Color"].default_value = col
    bsdf.inputs["Emission Strength"].default_value = float(strength)
    return mat


def volume(name: str, color: str | Sequence[float], density: float = 0.4, *, anisotropy: float = 0.3,
           noise_scale: float = 0.0, emission: float = 0.0) -> Any:
    """A Principled VOLUME with no surface: the only soft steam, smoke, dust, mist or spark haze
    Cycles can draw.  A sphere wearing it renders as a puff with no silhouette — a translucent
    Principled BSDF skin, a metaball or a disc always has one (``hard_edged_particles``, 8 of 30
    argon Blender scenes on 2026-10-15).  Measured on Blender 5.2.2: 40 subdivision-3 puffs add
    0.7 s to a 32-spp GPU frame.  ``density`` per metre — 0.15-0.35 steam and dust, 0.5-0.8 smoke;
    ``noise_scale`` features / m breaks a plume up (3-6); ``emission`` > 0 lights sparks or a haze
    from inside.  A volume is lit like everything else: white steam under a dim world comes out
    grey, so raise the world or give it ``emission`` 0.2-0.5."""
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    out = nodes["Material Output"]
    for n in [n for n in nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"]:
        nodes.remove(n)
    vol = nodes.new("ShaderNodeVolumePrincipled")
    col = _rgb(color)
    vol.inputs["Color"].default_value = col
    vol.inputs["Density"].default_value = float(density)
    vol.inputs["Anisotropy"].default_value = float(anisotropy)
    if emission > 0:
        vol.inputs["Emission Color"].default_value = col
        vol.inputs["Emission Strength"].default_value = float(emission)
    if noise_scale > 0:
        n = _noise(nodes, links, _coords(nodes, links, 1.0), scale=float(noise_scale), detail=4.0, roughness=0.6)
        mul = nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = float(density) * 2.0
        links.new(n.outputs["Fac"], mul.inputs[0])
        links.new(mul.outputs[0], vol.inputs["Density"])
    links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def tint(mat: Any, variant: float) -> Any:
    """A copy of ``mat`` whose base colour is shifted by ``variant`` (-1..1: darker / lighter
    and a touch of hue) — a crowd's coats, a street's shutters."""
    out = mat.copy()
    out.name = f"{mat.name}_v{variant:+.2f}"
    bsdf = out.node_tree.nodes.get("Principled BSDF")
    if bsdf is None or bsdf.inputs["Base Color"].is_linked:
        return out
    r, g, b, a = bsdf.inputs["Base Color"].default_value
    h, l_, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + 0.03 * variant) % 1.0, max(0.02, min(0.85, l_ * (1.0 + 0.3 * variant))), s)
    bsdf.inputs["Base Color"].default_value = (r, g, b, a)
    return out
