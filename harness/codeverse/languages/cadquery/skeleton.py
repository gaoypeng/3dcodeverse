"""Starter ``src/model.py`` for the cadquery language, generated from a plan.

Buildable as written: a ``cq.Assembly`` of placeholder boxes at the plan bboxes
(instances laid out by symmetry), each named and coloured, so the agent starts
from a green build and replaces ``build_<part>()`` bodies.  Raw cadquery only.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.plan import PartPlan, StaticPlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.languages.blender.skeleton import finish_for, instance_centers
from codeverse.workspace import Workspace


def _fmt(v: tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{x:.3f}" for x in v) + ")"


def _part_function(p: PartPlan) -> str:
    pascal, snake = to_pascal(p.name), to_snake(p.name)
    mn, mx = p.bbox.min, p.bbox.max
    ex, ey, ez = p.bbox.extents
    lines = [
        f"def build_{snake}():",
        f'    """{pascal} — {p.role}',
        f"    {p.description}",
        f"    Material: {p.material or 'n/a'}",
        f"    Plan bbox (world, Z-up): center {_fmt(p.bbox.center)} extents {_fmt(p.bbox.extents)}",
        f"      x in [{mn[0]:.3f}, {mx[0]:.3f}]  y in [{mn[1]:.3f}, {mx[1]:.3f}]  z in [{mn[2]:.3f}, {mx[2]:.3f}]",
    ]
    if p.attach_to:
        lines.append(f"    Attaches to: {to_pascal(p.attach_to)} (faces must touch; small overlaps are fine)")
    if p.instances > 1:
        lines.append(f"    Instances: {p.instances} ({p.symmetry}); build ONE centred at the origin, main() places each copy.")
        lines.append('    """')
        lines.append("    # TODO: replace the placeholder box (keep it centred on the origin; placement happens in main())")
        lines.append(f"    return cq.Workplane(\"XY\").box({ex:.3f}, {ey:.3f}, {ez:.3f})")
    else:
        lines.append('    """')
        lines.append("    # TODO: replace the placeholder box with the real geometry (keep the name + bbox)")
        lines.append(f"    return cq.Workplane(\"XY\").box({ex:.3f}, {ey:.3f}, {ez:.3f}).translate({_fmt(p.bbox.center)})")
    lines += ["", ""]
    return "\n".join(lines)


def _main_body(plan: StaticPlan) -> str:
    lines = [f'result = cq.Assembly(name="{to_pascal(plan.object_name)}")', ""]
    for p in plan.parts:
        pascal, snake = to_pascal(p.name), to_snake(p.name)
        rgb, _r, _m = finish_for(f"{p.material} {p.description}")
        color = f"cq.Color({rgb[0]:.2f}, {rgb[1]:.2f}, {rgb[2]:.2f})"
        if p.instances == 1:
            lines.append(f'result.add(build_{snake}(), name="{pascal}", color={color})')
            continue
        centers = instance_centers(p.bbox, p.instances, p.symmetry)
        lines.append(f"_{snake} = build_{snake}()")
        lines.append(f"for _i, _c in enumerate({[tuple(round(x, 4) for x in c) for c in centers]}):  # TODO: exact placement")
        lines.append(f'    result.add(_{snake}, name=f"{pascal}_{{_i}}", loc=cq.Location(cq.Vector(*_c)), color={color})')
    lines.append("")
    return "\n".join(lines)


def cadquery_skeleton_source(plan: StaticPlan) -> str:
    """Return the complete starter ``model.py`` text for ``plan``."""
    ob = plan.overall_bbox
    parts_doc = "\n".join(
        f"  - {to_pascal(p.name)}{'' if p.instances == 1 else f' x{p.instances}'}: {p.role}; bbox center {_fmt(p.bbox.center)} extents {_fmt(p.bbox.extents)}"
        for p in plan.parts
    )
    accept = "\n".join(f"  - [{a.id}] {a.text}" for a in plan.acceptance) or "  (none listed)"
    header = f'''"""{plan.object_name} — CadQuery model.

{plan.summary}
Style: {plan.style_notes or "n/a"}

CONTRACT (the harness imports this file and exports `result` to GLB/STEP/STL itself):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center {_fmt(ob.center)} extents {_fmt(ob.extents)}
    -> x in [{ob.min[0]:.3f}, {ob.max[0]:.3f}]  y in [{ob.min[1]:.3f}, {ob.max[1]:.3f}]  z in [{ob.min[2]:.3f}, {ob.max[2]:.3f}]
  * `result` = cq.Assembly; one `.add(shape, name="PascalName", color=cq.Color(r, g, b))` per part,
    instances Name_0..Name_N-1 (a shape built at the origin + `loc=cq.Location(cq.Vector(x, y, z))`).
  * Only `import cadquery as cq` (+ math / random). No file IO, no export, no show_object.
  * `.box(x, y, z)` is centred; `.cylinder(height, radius)`; `.rotate(p1, p2, DEGREES)`;
    `.translate()/.rotate()` return NEW objects (reassign). Keep fillet radius < 0.45 x wall thickness.

PARTS:
{parts_doc}

ACCEPTANCE:
{accept}
"""
import math  # noqa: F401

import cadquery as cq


'''
    body = "".join(_part_function(p) for p in plan.parts)
    return header + body + _main_body(plan)


def write_cadquery_skeleton(ws: Workspace, plan: StaticPlan) -> list[Path]:
    ws.src.mkdir(parents=True, exist_ok=True)
    path = ws.src / "model.py"
    path.write_text(cadquery_skeleton_source(plan))
    return [path]
