"""Starter ``src/model.py`` for the blender language, generated from a plan.

The skeleton is *buildable as written*: every part is a placeholder box at its
plan bbox (instances laid out by the plan's symmetry), with a Principled BSDF
material, so the agent starts from a passing build and replaces bodies one
function at a time.  Only raw bpy in the output — no harness imports.
"""

from __future__ import annotations

import math
from pathlib import Path

from codeverse.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.workspace import Workspace

# keyword → (rgb, roughness, metallic) placeholder finishes so the first render is not all-grey
_FINISHES: tuple[tuple[tuple[str, ...], tuple[float, float, float], float, float], ...] = (
    (("wood", "oak", "walnut", "pine", "timber", "birch"), (0.55, 0.36, 0.20), 0.55, 0.0),
    (("steel", "chrome", "aluminium", "aluminum", "metal", "iron", "brass"), (0.75, 0.75, 0.78), 0.35, 1.0),
    (("fabric", "cloth", "cushion", "upholster", "velvet", "linen"), (0.45, 0.30, 0.30), 0.9, 0.0),
    (("leather",), (0.30, 0.16, 0.10), 0.6, 0.0),
    (("glass", "acrylic"), (0.85, 0.9, 0.95), 0.05, 0.0),
    (("rubber", "tire", "tyre"), (0.05, 0.05, 0.05), 0.9, 0.0),
    (("plastic", "abs", "nylon"), (0.9, 0.9, 0.9), 0.4, 0.0),
    (("black",), (0.05, 0.05, 0.05), 0.5, 0.0),
    (("white",), (0.9, 0.9, 0.9), 0.5, 0.0),
    (("red",), (0.7, 0.1, 0.1), 0.5, 0.0),
    (("blue",), (0.1, 0.2, 0.7), 0.5, 0.0),
    (("green",), (0.1, 0.5, 0.2), 0.5, 0.0),
)


def finish_for(text: str) -> tuple[tuple[float, float, float], float, float]:
    """Placeholder (rgb, roughness, metallic) from material words; neutral grey otherwise."""
    low = text.lower()
    for words, rgb, rough, metal in _FINISHES:
        if any(w in low for w in words):
            return rgb, rough, metal
    return (0.6, 0.6, 0.6), 0.5, 0.0


def instance_centers(bbox: BBox, n: int, symmetry: str) -> list[tuple[float, float, float]]:
    """Placeholder centres for ``n`` instances of a part using the plan's symmetry hint."""
    cx, cy, cz = bbox.center
    if n <= 1:
        return [(cx, cy, cz)]
    if n == 2 and symmetry == "mirror_x":
        return [(abs(cx), cy, cz), (-abs(cx), cy, cz)]
    if n == 2 and symmetry == "mirror_y":
        return [(cx, abs(cy), cz), (cx, -abs(cy), cz)]
    if n == 4 and symmetry in ("mirror_x", "mirror_y"):
        ax, ay = abs(cx), abs(cy)
        return [(ax, -ay, cz), (-ax, -ay, cz), (-ax, ay, cz), (ax, ay, cz)]
    if symmetry == "radial":
        r = math.hypot(cx, cy)
        a0 = math.atan2(cy, cx)
        return [(r * math.cos(a0 + 2 * math.pi * i / n), r * math.sin(a0 + 2 * math.pi * i / n), cz) for i in range(n)]
    return [(cx, cy, cz) for _ in range(n)]


def _fmt(v: tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{x:.3f}" for x in v) + ")"


def _part_function(p: PartPlan) -> str:
    pascal, snake = to_pascal(p.name), to_snake(p.name)
    rgb, rough, metal = finish_for(f"{p.material} {p.description}")
    mn, mx = p.bbox.min, p.bbox.max
    lines = [
        f"def build_{snake}(index=0, center={_fmt(p.bbox.center)}):",
        f'    """{pascal} — {p.role}',
        f"    {p.description}",
        f"    Material: {p.material or 'n/a'}",
        f"    Plan bbox: center {_fmt(p.bbox.center)} extents {_fmt(p.bbox.extents)}",
        f"      x in [{mn[0]:.3f}, {mx[0]:.3f}]  y in [{mn[1]:.3f}, {mx[1]:.3f}]  z in [{mn[2]:.3f}, {mx[2]:.3f}]",
    ]
    if p.attach_to:
        lines.append(f"    Attaches to: {to_pascal(p.attach_to)} (surfaces must touch, no gap)")
    if p.instances > 1:
        lines.append(f"    Instances: {p.instances} ({p.symmetry}) -> named {pascal}_0..{pascal}_{p.instances - 1}, parented to Empty '{pascal}s'")
    lines += [
        '    """',
        f'    name = "{pascal}"' if p.instances == 1 else f'    name = f"{pascal}_{{index}}"',
        f"    mat = make_material(name + \"Mat\", {_fmt(rgb)}, roughness={rough}, metallic={metal})",
        "    # TODO: replace this placeholder box with the real geometry (keep the name + bbox)",
        f"    return add_box(name, center, {_fmt(p.bbox.extents)}, mat, bevel={_bevel(p.bbox):.4f})",
        "",
        "",
    ]
    return "\n".join(lines)


def _bevel(bbox: BBox) -> float:
    return max(0.0, min(0.01, 0.08 * min(bbox.extents)))


def _main_body(plan: StaticPlan) -> str:
    lines = ["def main():"]
    for p in plan.parts:
        pascal, snake = to_pascal(p.name), to_snake(p.name)
        if p.instances == 1:
            lines.append(f"    build_{snake}()")
            continue
        centers = instance_centers(p.bbox, p.instances, p.symmetry)
        lines.append(f"    {snake}_parent = add_empty(\"{pascal}s\")")
        lines.append(f"    for i, c in enumerate({[tuple(round(x, 4) for x in c) for c in centers]}):  # TODO: exact placement")
        lines.append(f"        obj = build_{snake}(i, c)")
        lines.append(f"        obj.parent = {snake}_parent")
    lines += ["", "", "main()", ""]
    return "\n".join(lines)


def blender_skeleton_source(plan: StaticPlan) -> str:
    """Return the complete starter ``model.py`` text for ``plan``."""
    ob = plan.overall_bbox
    parts_doc = "\n".join(
        f"  - {to_pascal(p.name)}{'' if p.instances == 1 else f' x{p.instances}'}: {p.role}; bbox center {_fmt(p.bbox.center)} extents {_fmt(p.bbox.extents)}"
        for p in plan.parts
    )
    accept = "\n".join(f"  - [{a.id}] {a.text}" for a in plan.acceptance) or "  (none listed)"
    header = f'''"""{plan.object_name} — Blender (bpy) model.

{plan.summary}
Style: {plan.style_notes or "n/a"}

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center {_fmt(ob.center)} extents {_fmt(ob.extents)}
    -> x in [{ob.min[0]:.3f}, {ob.max[0]:.3f}]  y in [{ob.min[1]:.3f}, {ob.max[1]:.3f}]  z in [{ob.min[2]:.3f}, {ob.max[2]:.3f}]
  * One mesh object per part, named EXACTLY as below (PascalCase); instances Name_0..Name_N-1.
  * Materials: Principled BSDF (Base Color / Roughness / Metallic). GLB keeps flat PBR + image
    textures only (procedural node textures are NOT exported) — rely on geometry + flat PBR.
  * Modifiers may stay unapplied (the exporter applies them). Keep < 500k triangles, < 120 s.
  * NEVER: cameras, lights, world, render settings, export/import, file IO, bpy.ops.wm.*.
  * Only bpy / bmesh / mathutils / math / random (seeded). No other imports.

PARTS:
{parts_doc}

ACCEPTANCE:
{accept}
"""
import math
import random

import bpy
import bmesh  # noqa: F401  (available for custom meshes: bm = bmesh.new(); ... bm.to_mesh(me))
from mathutils import Vector, Matrix  # noqa: F401

random.seed(0)


# ----------------------------------------------------------------------------- helpers (plain bpy)
def make_material(name, rgb, roughness=0.5, metallic=0.0):
    """Principled BSDF material with flat PBR values (what the GLB keeps)."""
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def add_box(name, center, extents, material=None, bevel=0.0):
    """Axis-aligned box: size=1 cube scaled to `extents`, scale baked into the mesh."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=center, scale=extents)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if material is not None:
        obj.data.materials.append(material)
    if bevel > 0:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        mod.limit_method = "ANGLE"
    return obj


def add_empty(name):
    """Parent Empty for instance groups (exported as a named node)."""
    empty = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(empty)
    return empty


# ----------------------------------------------------------------------------- parts
'''
    body = "".join(_part_function(p) for p in plan.parts)
    return header + body + _main_body(plan)


def write_blender_skeleton(ws: Workspace, plan: StaticPlan) -> list[Path]:
    """Write ``src/model.py`` (overwrites) and return the written paths."""
    ws.src.mkdir(parents=True, exist_ok=True)
    path = ws.src / "model.py"
    path.write_text(blender_skeleton_source(plan))
    return [path]
