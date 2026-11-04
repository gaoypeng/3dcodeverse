"""Reference comparison: judge a render against the user's own ``--image`` pictures.

The user's reference images ride in ``spec.references``.  This module is what
measures and names the distance between one of them and a render: the brief's
dimension band vs the picture's outline (:func:`dimension_conflict` — when they
disagree the BRIEF wins and the picture is a shape target only), and one vision
call that lists concrete mismatches (:func:`compare`).  The planner, the
generator's ``compare_reference`` tool, the silhouette gate and the judge
(:class:`codeverse3d.judges.vlm_judge.ReferenceJudge`) all read it.  Nothing here
generates an image: the harness has no image-generation step.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.spec import Spec
from codeverse3d.models.schema_utils import ask_structured

log = logging.getLogger(__name__)


# ===================================================================== types
MismatchKind = Literal[
    "missing_feature", "extra_feature", "wrong_part_count", "wrong_proportion",
    "wrong_shape", "wrong_material", "wrong_placement",
]

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}


class Mismatch(BaseModel):
    """One concrete difference between the reference and the render."""

    kind: MismatchKind
    target: str = Field(default="overall", description="part name from the plan, or 'overall'")
    detail: str = Field(description="what the reference shows vs what the render shows")
    severity: Literal["critical", "major", "minor"] = "major"

    def as_line(self) -> str:
        return f"[{self.severity}] {self.target} — {self.kind.replace('_', ' ')}: {self.detail}"


class ReferenceDiff(BaseModel):
    """Render-vs-reference comparison: measured IoU + named mismatches."""

    iou: float | None = None
    aspect_ratio_err: float | None = None
    reliable: bool = True
    view: str = ""
    reference: str = ""
    mismatches: list[Mismatch] = Field(default_factory=list)
    matches: list[str] = Field(default_factory=list, description="what the render already gets right")
    model_id: str = ""
    usage: Usage = Field(default_factory=Usage)
    error: str = ""

    def top(self, n: int = 3) -> list[Mismatch]:
        """The ``n`` mismatches worth spending a refine task on (severity first)."""
        return sorted(self.mismatches, key=lambda m: _SEVERITY_ORDER.get(m.severity, 3))[:n]

    def as_text(self, n: int = 6) -> str:
        """Compact block for a judge / agent prompt (empty when nothing to say)."""
        if self.error or not (self.mismatches or self.matches):
            return ""
        lines = ["REFERENCE DIFF (harness vision pass, target = the REFERENCE image):"]
        if self.iou is not None:
            lines.append(f"- measured silhouette IoU {self.iou:.3f}"
                         + (f", aspect error {self.aspect_ratio_err:.0%}" if self.aspect_ratio_err is not None else "")
                         + ("" if self.reliable else " (mask unreliable — judge visually)"))
        for m in self.top(n):
            lines.append(f"- {m.as_line()}")
        if self.matches:
            lines.append("- already matching: " + "; ".join(self.matches[:4]))
        return "\n".join(lines)


# ===================================================================== spec_text
def brief_text(spec: Spec) -> str:
    """The prompt + the constraints a picture could show, as one block."""
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


# --------------------------------------------------------------------------- reference diff
DIFF_SYSTEM = (
    "You compare a 3D render against a reference image and name concrete, fixable differences. "
    "You never comment on background, lighting, camera framing or image style — only on the object's "
    "shape, its parts, their counts, their proportions, their placement and their materials. "
    "Answer with JSON only."
)

DIFF_USER = """\
The images labelled REFERENCE are the shape target.  The images labelled RENDER are the current 3D model.

BRIEF
-----
{brief}
{measured}
List the differences that make the RENDER read as less like the real object than the REFERENCE does.
For each, give:
- `kind`: missing_feature | extra_feature | wrong_part_count | wrong_proportion | wrong_shape |
  wrong_material | wrong_placement
- `target`: the part name it concerns (use a name from this list when one fits: {parts}), else "overall"
- `detail`: what the REFERENCE shows and what the RENDER shows instead — one sentence, concrete and
  checkable (name the count, the ratio or the shape, e.g. "reference has 8 flutes around the body,
  render has a smooth cylinder")
- `severity`: critical (the object no longer reads as the right thing) | major | minor

Rules:
- At most 6 mismatches; order them so the most identity-destroying comes first.
- Ignore anything the brief overrides: where the brief states a dimension, a count or a material, the BRIEF
  wins and the reference does not.
- Do not report background, lighting, shadow, reflection, resolution or camera-angle differences.
- `matches`: up to 4 short phrases naming what the render already gets right (so the builder does not undo it).
"""


# ===================================================================== proportions

#: relative aspect disagreement above which the picture's proportions are not a target
ASPECT_TOL = 0.25

#: brief keys that describe a HORIZONTAL extent of the object
_WIDTH_KEYS = ("width", "length", "depth", "diameter")


def expected_aspect_range(spec: Spec) -> tuple[float, float] | None:
    """The width/height band any straight-on elevation of the brief could show.

    A brief does not say which face a photograph shows, so the honest expectation
    is a RANGE: the narrowest and the widest horizontal extent the brief states,
    each over the stated height.  Only a picture outside that band by more than
    :data:`ASPECT_TOL` disagrees with the brief — which keeps a chair whose photo
    is a little wide from being flagged while still catching a 0.14 m grinder body
    photographed with its 0.12 m crank arm.
    """
    dims = spec.constraints.dimensions_m or {}
    height = dims.get("height")
    if not height or height <= 0:
        return None
    widths = [float(dims[k]) for k in _WIDTH_KEYS if dims.get(k) and float(dims[k]) > 0]
    if not widths:
        return None
    return min(widths) / float(height), max(widths) / float(height)


def expected_front_aspect(spec: Spec) -> float | None:
    """Mid-point of :func:`expected_aspect_range` (``None`` if unstated)."""
    rng = expected_aspect_range(spec)
    return None if rng is None else (rng[0] + rng[1]) / 2


def dimension_conflict(spec: Spec, image: Path | str) -> dict[str, Any]:
    """``{conflict, expected_aspect, image_aspect, error}`` — never raises.

    ``conflict`` is True only when the brief states a height plus one horizontal
    dimension AND the image's own silhouette aspect is off by more than
    :data:`ASPECT_TOL`.  An unmeasurable image is never a conflict.
    """
    rng = expected_aspect_range(spec)
    out: dict[str, Any] = {"conflict": False, "expected_aspect": expected_front_aspect(spec),
                           "expected_range": list(rng) if rng else None, "image_aspect": None, "error": ""}
    if rng is None:
        return out
    low, high = rng
    try:
        from codeverse3d.spatial.silhouette import silhouette_aspect

        got = float(silhouette_aspect(image))
    except Exception as e:  # noqa: BLE001 — an advisory check must never fail a run
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["image_aspect"] = round(got, 4)
    if got <= 0:
        out["error"] = "empty foreground mask"
        return out
    off = 0.0 if low <= got <= high else (low - got) / low if got < low else (got - high) / high
    out["relative_error"] = round(off, 4)
    out["conflict"] = off > ASPECT_TOL
    return out


def conflict_note(info: dict[str, Any]) -> str:
    """One line for a judge / CLI, empty when there is no conflict."""
    if not info.get("conflict"):
        return ""
    lo, hi = info.get("expected_range") or (0.0, 0.0)
    return (f"REFERENCE PROPORTIONS DISAGREE WITH THE BRIEF: the picture's outline is "
            f"{info['image_aspect']:.2f} wide/high, outside the {lo:.2f}-{hi:.2f} band the stated dimensions "
            f"allow ({info['relative_error']:.0%} outside it) — usually a part reaching outside the stated box, "
            f"or the object resting in a different orientation. The BRIEF's dimensions are correct; use the "
            f"reference for the part inventory and the shape of each part, NOT for the overall proportions. "
            f"The measured silhouette score is therefore neutral and must not be read as a proportion verdict.")

# ===================================================================== mismatch

MAX_MISMATCHES = 6


class DiffAnswer(BaseModel):
    mismatches: list[Mismatch] = Field(default_factory=list)
    matches: list[str] = Field(default_factory=list)


def compare(
    spec: Spec,
    reference_images: Sequence[Path | str],
    render_images: Sequence[Path | str],
    *,
    model: Any,
    part_names: Sequence[str] = (),
    measured: dict[str, Any] | None = None,
    temperature: float = 0.1,
    max_reference: int = 2,
    max_renders: int = 3,
) -> ReferenceDiff:
    """One vision call: REFERENCE images + RENDER images → named mismatches."""
    refs = [Path(p) for p in reference_images if Path(p).is_file()][:max_reference]
    rens = [Path(p) for p in render_images if Path(p).is_file()][:max_renders]
    diff = ReferenceDiff(model_id=getattr(model, "id", "") if model else "")
    if measured:
        diff.iou = _as_float(measured.get("iou"))
        diff.aspect_ratio_err = _as_float(measured.get("aspect_ratio_err"))
        diff.reliable = bool(measured.get("reliable", True))
        diff.view = str(measured.get("view", "") or measured.get("render", ""))
        diff.reference = str(measured.get("reference", ""))
    if not refs or not rens:
        diff.error = "nothing to compare (missing reference or render images)"
        return diff
    if model is None:
        diff.error = "no vision model for the reference diff"
        return diff
    text = DIFF_USER.format(
        brief=brief_text(spec),
        measured=_measured_block(diff),
        parts=", ".join(list(part_names)[:40]) or "(no part list)",
    )
    images = [ImagePart(path=str(p), label=f"REFERENCE {i + 1}") for i, p in enumerate(refs)]
    images += [ImagePart(path=str(p), label=f"RENDER {i + 1} ({p.stem})") for i, p in enumerate(rens)]
    ans, usage, err = ask_structured(model, DiffAnswer, system=DIFF_SYSTEM, text=text, images=images,
                           temperature=temperature, label="reference_diff")
    diff.usage = usage
    if err:
        log.warning("reference diff %s", err)
        diff.error = f"diff {err}"
        return diff
    diff.mismatches = ans.mismatches[:MAX_MISMATCHES]
    diff.matches = [m.strip()[:120] for m in ans.matches if m.strip()][:4]
    return diff


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _measured_block(diff: ReferenceDiff) -> str:
    if diff.iou is None:
        return ""
    line = f"\nMEASURED BY THE HARNESS: outline IoU of the front render vs the reference = {diff.iou:.3f}"
    if diff.aspect_ratio_err is not None:
        line += f", width/height error {diff.aspect_ratio_err:.0%}"
    if not diff.reliable:
        line += " (background mask unreliable — trust your eyes over this number)"
    return line + ".  This is a fact; do not re-estimate it.\n"
