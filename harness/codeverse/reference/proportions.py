"""Does the picture agree with the brief about PROPORTIONS?

A synthesized product shot is a shape target, not a measurement.  When the brief
states dimensions and the image disagrees with them — the classic case is a part
that sticks out of the stated box, e.g. a coffee grinder whose 0.14 m body comes
with a 0.12 m crank arm — every *numeric* proportion signal derived from that
image (silhouette IoU, aspect-ratio error, "make it wider") points AWAY from the
brief.  The brief always wins, so those signals must be neutralised rather than
followed.

:func:`dimension_conflict` is that check.  It is cheap (one foreground mask) and
deterministic; :class:`codeverse.judges.reference.ReferenceJudge` uses it to score
the measured silhouette criterion neutrally instead of punishing an object for
obeying its own brief.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from codeverse.contracts.spec import Spec

log = logging.getLogger(__name__)

#: relative aspect disagreement above which the picture's proportions are not a target
ASPECT_TOL = 0.25
#: the measured silhouette criterion's score when the reference contradicts the brief
NEUTRAL_SCORE = 0.5

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
        from codeverse.spatial.silhouette import silhouette_aspect

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


__all__ = ["ASPECT_TOL", "NEUTRAL_SCORE", "conflict_note", "dimension_conflict", "expected_aspect_range",
           "expected_front_aspect"]
