"""Spec → the two text blocks every reference call needs.

Local on purpose (``tracks.prompting.constraints_text`` is authored by another
wave and this package must not couple to a file being edited elsewhere), and
deliberately narrow: only what a *picture* can be held to.
"""

from __future__ import annotations

from codeverse.contracts.spec import Spec


def brief_text(spec: Spec) -> str:
    """Prompt + the constraints a photograph could show, as one block."""
    lines = [spec.prompt.strip()]
    c = spec.constraints
    if c.style:
        lines.append(f"Style: {c.style}")
    if c.dimensions_m:
        lines.append("Overall dimensions (metres): "
                     + ", ".join(f"{k} {v:g}" for k, v in sorted(c.dimensions_m.items())))
    for m in c.must_have:
        lines.append(f"MUST HAVE: {m}")
    for m in c.must_not:
        lines.append(f"MUST NOT: {m}")
    return "\n".join(lines)


def visual_constraints(spec: Spec) -> str:
    """The constraints the plausibility gate may hold an IMAGE to.

    Absolute dimensions are quoted as *ratios* only: a photograph with no scale
    reference cannot contradict "height 0.30 m", but it can contradict
    "twice as tall as it is wide".  ``max_triangles`` is not visual at all.
    """
    c = spec.constraints
    lines: list[str] = []
    dims = c.dimensions_m or {}
    height = dims.get("height")
    for name in ("width", "depth", "length"):
        other = dims.get(name)
        if height and other:
            lines.append(f"proportion: height : {name} ≈ {height / other:.2f} : 1")
    for m in c.must_have:
        lines.append(f"must be visible: {m}")
    for m in c.must_not:
        lines.append(f"must NOT be present: {m}")
    if c.style:
        lines.append(f"style: {c.style}")
    return "\n".join(f"- {line}" for line in lines) or "- (none stated)"


__all__ = ["brief_text", "visual_constraints"]
