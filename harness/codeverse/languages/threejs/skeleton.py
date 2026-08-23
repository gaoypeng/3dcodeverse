"""Starter files for a three.js static object from a StaticPlan.

Every part gets a stub that already BUILDS (a rounded-box placeholder at the
planned bbox) so the skeleton exports and renders before the agent touches it;
the agent replaces placeholders part by part and the gates show what is left.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.languages.threejs.templates import (
    OBJECT_HEADER,
    PACKAGE_JSON,
    PART_TEMPLATE,
    PLACEHOLDER_COLORS,
)
from codeverse.workspace import Workspace


def part_file(ws: Workspace, part_name: str) -> Path:
    """``src/parts/<snake>.js`` for a plan part name."""
    return ws.src / "parts" / f"{to_snake(part_name)}.js"


def _fmt(v: float) -> str:
    return f"{float(v):.4g}"


def render_part_stub(part, index: int) -> str:
    """JS source of one placeholder part file."""
    c, e = part.bbox.center, part.bbox.extents
    material_line = f"\n// Material: {part.material}" if part.material else ""
    return PART_TEMPLATE.format(
        pascal=to_pascal(part.name),
        role=part.role,
        description=part.description.replace("\n", " "),
        center=f"({_fmt(c[0])}, {_fmt(c[1])}, {_fmt(c[2])})",
        extents=f"({_fmt(e[0])}, {_fmt(e[1])}, {_fmt(e[2])})",
        material_line=material_line,
        ex=_fmt(max(e[0], 0.001)),
        ey=_fmt(max(e[1], 0.001)),
        ez=_fmt(max(e[2], 0.001)),
        cx=_fmt(c[0]),
        cy=_fmt(c[1]),
        cz=_fmt(c[2]),
        color=PLACEHOLDER_COLORS[index % len(PLACEHOLDER_COLORS)],
    )


def render_object(plan: StaticPlan) -> str:
    imports = "\n".join(
        f"import {{ build{to_pascal(p.name)} }} from './parts/{to_snake(p.name)}.js';" for p in plan.parts
    )
    adds = "\n".join(f"  root.add(build{to_pascal(p.name)}(THREE));" for p in plan.parts)
    return OBJECT_HEADER.format(object_name=to_pascal(plan.object_name), imports=imports, adds=adds)


def write_skeleton(ws: Workspace, plan: Plan, *, overwrite: bool = False) -> list[Path]:
    """Write ``src/package.json``, ``src/object.js`` and ``src/parts/*.js``; return written paths.

    Existing files are kept unless ``overwrite`` (so re-running on a refined
    workspace never clobbers agent work).
    """
    if not isinstance(plan, StaticPlan):
        raise TypeError(f"threejs skeleton needs a StaticPlan, got {type(plan).__name__}")
    written: list[Path] = []
    (ws.src / "parts").mkdir(parents=True, exist_ok=True)

    def _put(path: Path, text: str) -> None:
        if path.exists() and not overwrite:
            return
        path.write_text(text)
        written.append(path)

    _put(ws.src / "package.json", PACKAGE_JSON)
    _put(ws.src / "object.js", render_object(plan))
    for i, part in enumerate(plan.parts):
        _put(part_file(ws, part.name), render_part_stub(part, i))
    return written
