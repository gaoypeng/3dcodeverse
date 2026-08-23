"""Starter files for the blender language, generated from a plan.

Default layout (multi-file, refinable part-by-part in parallel):

* ``src/parts/<snake>.py`` — one per plan part.  Self-contained (own tiny helpers),
  plan numbers as constants on top, description/role/material/attach_to in the
  docstring, and ``def build_<snake>() -> bpy.types.Object`` that builds a placeholder
  box at the plan bbox (instances ``Name_0..N-1`` as TOP-LEVEL objects — never under a
  parent Empty: the harness measures top-level GLB nodes as parts, and an Empty parent
  would merge all instances into one part).
* ``src/model.py`` — entry: imports the builders, calls them in order, self-checks.

``blender_skeleton_source(plan)`` still renders the same model as ONE file (small
objects may use a single ``src/model.py``).  Both layouts build as written: the agent
starts from a passing build and replaces placeholder bodies one function at a time.
Only raw bpy in the output — no harness imports.
"""

from __future__ import annotations

import math
from pathlib import Path

from codeverse.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.languages.blender.layout import PARTS_DIR, PARTS_PKG, build_fn_name, part_file_rel
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


def _bevel(bbox: BBox) -> float:
    return max(0.0, min(0.01, 0.08 * min(bbox.extents)))


# ----------------------------------------------------------------------------- shared text blocks
IMPORTS = '''import math
import random

import bpy
import bmesh  # noqa: F401  (available for custom meshes: bm = bmesh.new(); ... bm.to_mesh(me))
from mathutils import Vector, Matrix  # noqa: F401

random.seed(0)
'''

HELPERS = '''
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

'''

SELFCHECK = '''
def _selfcheck():
    """What the harness checks first: meshes exist, no auto-suffixed names, stands on z=0."""
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert meshes, "no mesh objects built"
    for o in meshes:
        assert "." not in o.name, f"auto-suffixed name {o.name!r}: give every instance its own name"
    z_min = min((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box)
    assert abs(z_min) < 0.002, f"lowest point z={z_min:.4f}: the object must stand on z=0"
    print(f"[selfcheck] {len(meshes)} mesh objects, z_min={z_min:.4f}")

'''


def _constants(p: PartPlan) -> str:
    mn, mx = p.bbox.min, p.bbox.max
    pre = to_snake(p.name).upper()
    return (
        f"# ---- plan numbers (metres) for {to_pascal(p.name)} — keep the finished part inside this bbox\n"
        f"{pre}_CENTER = {_fmt(p.bbox.center)}\n"
        f"{pre}_EXTENTS = {_fmt(p.bbox.extents)}\n"
        f"{pre}_MIN = {_fmt(mn)}\n"
        f"{pre}_MAX = {_fmt(mx)}\n"
        f"{pre}_INSTANCES = {p.instances}\n"
    )


def _part_function(p: PartPlan) -> str:
    """``def build_<snake>() -> bpy.types.Object`` (placeholder box; instances looped inside)."""
    pascal, snake, fn = to_pascal(p.name), to_snake(p.name), build_fn_name(p.name)
    pre = snake.upper()
    rgb, rough, metal = finish_for(f"{p.material} {p.description}")
    mn, mx = p.bbox.min, p.bbox.max
    lines = [
        f"def {fn}():",
        f'    """{pascal} — {p.role}',
        f"    {p.description}",
        f"    Material: {p.material or 'n/a'}",
        f"    Plan bbox: center {_fmt(p.bbox.center)} extents {_fmt(p.bbox.extents)}",
        f"      x in [{mn[0]:.3f}, {mx[0]:.3f}]  y in [{mn[1]:.3f}, {mx[1]:.3f}]  z in [{mn[2]:.3f}, {mx[2]:.3f}]",
    ]
    if p.attach_to:
        lines.append(f"    Attaches to: {to_pascal(p.attach_to)} (surfaces must touch, no gap)")
    if p.instances > 1:
        lines.append(f"    Instances: {p.instances} ({p.symmetry}) -> named {pascal}_0..{pascal}_{p.instances - 1}, "
                     f"each TOP-LEVEL (no parent Empty — it would merge them into one measured part); returns the list")
    lines += ['    Returns the object(s) at WORLD pose (Z up, -Y front, metres).', '    """']
    lines.append(f"    mat = make_material(\"{pascal}Mat\", {_fmt(rgb)}, roughness={rough}, metallic={metal})")
    if p.instances == 1:
        lines += [
            "    # TODO: replace this placeholder box with the real geometry (keep the name + bbox)",
            f"    return add_box(\"{pascal}\", {pre}_CENTER, {pre}_EXTENTS, mat, bevel={_bevel(p.bbox):.4f})",
        ]
    else:
        centers = [tuple(round(x, 4) for x in c) for c in instance_centers(p.bbox, p.instances, p.symmetry)]
        lines += [
            "    objs = []",
            f"    for i, c in enumerate({centers}):  # TODO: exact placement",
            "        # TODO: replace this placeholder box with the real geometry (keep the names + bbox)",
            f"        objs.append(add_box(f\"{pascal}_{{i}}\", c, {pre}_EXTENTS, mat, bevel={_bevel(p.bbox):.4f}))",
            "    return objs",
        ]
    return "\n".join(lines) + "\n"


def _plan_header(plan: StaticPlan, *, multi_file: bool) -> str:
    ob = plan.overall_bbox
    parts_doc = "\n".join(
        f"  - {to_pascal(p.name)}{'' if p.instances == 1 else f' x{p.instances}'}: {p.role}; bbox center {_fmt(p.bbox.center)} "
        f"extents {_fmt(p.bbox.extents)}" + (f"  [{part_file_rel(p.name)}]" if multi_file else "")
        for p in plan.parts
    )
    accept = "\n".join(f"  - [{a.id}] {a.text}" for a in plan.acceptance) or "  (none listed)"
    layout = (
        f"  * LAYOUT: this file is the ENTRY. Each part lives in src/{PARTS_DIR}/<snake>.py and exports\n"
        f"    build_<snake>() -> bpy.types.Object; main() below imports and calls them in order.\n"
        "    Edit geometry in the part files; keep this file to imports + calls + self-check.\n"
        if multi_file else
        "  * LAYOUT: single file (small object). One build_<snake>() per part, called from main().\n"
    )
    return f'''"""{plan.object_name} — Blender (bpy) model.

{plan.summary}
Style: {plan.style_notes or "n/a"}

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center {_fmt(ob.center)} extents {_fmt(ob.extents)}
    -> x in [{ob.min[0]:.3f}, {ob.max[0]:.3f}]  y in [{ob.min[1]:.3f}, {ob.max[1]:.3f}]  z in [{ob.min[2]:.3f}, {ob.max[2]:.3f}]
  * One mesh object per part, named EXACTLY as below (PascalCase); instances Name_0..Name_N-1,
    each TOP-LEVEL (never parented under an Empty — that merges them into ONE measured part).
{layout}  * Materials: Principled BSDF (Base Color / Roughness / Metallic). GLB keeps flat PBR + image
    textures only (procedural node textures are NOT exported) — rely on geometry + flat PBR.
  * Modifiers may stay unapplied (the exporter applies them). Keep < 500k triangles, < 120 s.
  * NEVER: cameras, lights, world, render settings, export/import, file IO, bpy.ops.wm.*.
  * Only bpy / bmesh / mathutils / math / random (seeded). No other imports.

PARTS:
{parts_doc}

ACCEPTANCE:
{accept}
"""
'''


# ----------------------------------------------------------------------------- public renderers
def part_file_source(p: PartPlan) -> str:
    """Complete ``src/parts/<snake>.py`` text for one plan part (self-contained, runnable)."""
    pascal, fn = to_pascal(p.name), build_fn_name(p.name)
    doc = (
        f'"""{pascal} — {p.role} (part module; imported by src/model.py).\n\n'
        f"{p.description}\n"
        f"Material: {p.material or 'n/a'}.  Instances: {p.instances}"
        + (f" ({p.symmetry})" if p.instances > 1 else "")
        + (f".  Attaches to: {to_pascal(p.attach_to)} (must touch, no gap)" if p.attach_to else "")
        + f".\n\nExports `{fn}() -> bpy.types.Object`: builds the part at its WORLD pose (Z up, -Y front,\n"
        "metres) with the exact object name(s) and returns it (list of TOP-LEVEL objects when\n"
        "instanced — never parent instances under an Empty).  Do NOT call it here — model.py does.\n"
        "Keep this file self-contained (its own helpers); only bpy/bmesh/mathutils/math/random.\n"
        '"""\n'
    )
    return doc + IMPORTS + "\n" + _constants(p) + HELPERS + "\n# ----------------------------------------------------------------------------- part\n" + _part_function(p)


def model_file_source(plan: StaticPlan) -> str:
    """Complete multi-file entry ``src/model.py``: imports, ordered calls, self-check."""
    imports = "\n".join(f"from {PARTS_PKG}.{to_snake(p.name)} import {build_fn_name(p.name)}" for p in plan.parts)
    calls = "\n".join(f"    {build_fn_name(p.name)}()" for p in plan.parts)
    return (
        _plan_header(plan, multi_file=True)
        + "import bpy\nfrom mathutils import Vector\n\n"
        + imports + "\n\n"
        + SELFCHECK
        + "\ndef main():\n    # build every part (order = plan order); parts are placed at world pose by their builders\n"
        + calls + "\n    _selfcheck()\n\n\nmain()\n"
    )


def blender_skeleton_source(plan: StaticPlan) -> str:
    """The same model as ONE ``src/model.py`` (small objects may use a single file)."""
    body = "\n# ----------------------------------------------------------------------------- parts\n"
    for p in plan.parts:
        body += _constants(p) + "\n\n" + _part_function(p) + "\n\n"
    calls = "\n".join(f"    {build_fn_name(p.name)}()" for p in plan.parts)
    return (
        _plan_header(plan, multi_file=False) + IMPORTS + HELPERS + body + SELFCHECK
        + "\ndef main():\n" + calls + "\n    _selfcheck()\n\n\nmain()\n"
    )


def write_blender_skeleton(ws: Workspace, plan: StaticPlan, *, multi_file: bool = True) -> list[Path]:
    """Write the starter files (overwrites) and return the written paths (entry first)."""
    ws.src.mkdir(parents=True, exist_ok=True)
    entry = ws.src / "model.py"
    if not multi_file:
        entry.write_text(blender_skeleton_source(plan))
        return [entry]
    entry.write_text(model_file_source(plan))
    written = [entry]
    for p in plan.parts:
        path = ws.root / part_file_rel(p.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(part_file_source(p))
        written.append(path)
    return written
