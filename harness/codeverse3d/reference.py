"""Reference comparison: the brief's dimension band vs the outline of the user's own ``--image``
picture (:func:`dimension_conflict`; the BRIEF wins on a conflict)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codeverse3d.contracts.spec import Spec

# ===================================================================== proportions

#: relative aspect disagreement above which the picture's proportions are not a target
ASPECT_TOL = 0.25

#: brief keys that describe a HORIZONTAL extent of the object
_WIDTH_KEYS = ("width", "length", "depth", "diameter")


def expected_aspect_range(spec: Spec) -> tuple[float, float] | None:
    """The width/height band any straight-on elevation of the brief could show (a RANGE, since the
    brief does not say which face a photograph shows)."""
    dims = spec.constraints.dimensions_m or {}
    height = dims.get("height")
    if not height or height <= 0:
        return None
    widths = [float(dims[k]) for k in _WIDTH_KEYS if dims.get(k) and float(dims[k]) > 0]
    if not widths:
        return None
    return min(widths) / float(height), max(widths) / float(height)


def expected_front_aspect(spec: Spec) -> float | None:
    rng = expected_aspect_range(spec)
    return None if rng is None else (rng[0] + rng[1]) / 2


def dimension_conflict(spec: Spec, image: Path | str) -> dict[str, Any]:
    """``{conflict, expected_aspect, image_aspect, error}`` — never raises; conflict only when the
    image's silhouette aspect is off the brief's band by more than :data:`ASPECT_TOL`."""
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
    if not info.get("conflict"):
        return ""
    lo, hi = info.get("expected_range") or (0.0, 0.0)
    return (f"REFERENCE PROPORTIONS DISAGREE WITH THE BRIEF: the picture's outline is "
            f"{info['image_aspect']:.2f} wide/high, outside the {lo:.2f}-{hi:.2f} band the stated dimensions "
            f"allow ({info['relative_error']:.0%} outside it) — usually a part reaching outside the stated box, "
            f"or the object resting in a different orientation. The BRIEF's dimensions are correct; use the "
            f"reference for the part inventory and the shape of each part, NOT for the overall proportions. "
            f"The measured silhouette score is therefore neutral and must not be read as a proportion verdict.")
