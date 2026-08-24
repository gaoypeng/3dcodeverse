"""Human-readable labels for the gallery.

Two small vocabularies live here so the page renderers never invent wording:

* :func:`humanize_view` — the render pipeline's machine view names
  (``front_right_34``, ``t=2.5s``, ``pose_door_hinge@upper``) become the words a
  person would say ("Front Right ¾", "t = 2.5 s", "Pose · door_hinge@upper");
* :data:`VERDICTS` — the four **disjoint** triage buckets every run falls into
  exactly once, so a status breakdown always sums to the number of runs shown.
"""

from __future__ import annotations

#: the four buckets, in the order they are always displayed
VERDICTS = ("passed", "failed", "unjudged", "error")

#: bucket → (short label, css class, what it means)
VERDICT_META: dict[str, tuple[str, str, str]] = {
    "passed": ("passed", "v-pass", "judged and over the rubric threshold"),
    "failed": ("failed", "v-fail", "judged and under the rubric threshold"),
    "unjudged": ("unjudged", "v-none", "the run finished but has no verdict"),
    "error": ("error", "v-err", "no record.json, or a corrupt / half-written one"),
}

#: view-name tokens that are not words
_TOKENS = {"34": "¾", "3q": "¾", "hdri": "HDRI", "uv": "UV", "ao": "AO", "id": "ID"}

#: whole names the render pipeline emits that deserve a written-out label
_WHOLE = {
    "articulation_sheet": "Articulation poses",
    "articulation": "Articulation poses",
    "preview.gif": "Animated preview",
    "preview": "Animated preview",
    "sheet": "Contact sheet",
    "wire": "Wireframe",
    "normals": "Normals",
    "depth": "Depth",
}


def _word(token: str) -> str:
    low = token.lower()
    if low in _TOKENS:
        return _TOKENS[low]
    if not token:
        return token
    return token[:1].upper() + token[1:] if token[:1].islower() else token


def humanize_view(name: str) -> str:
    """A render view's machine name as a person would read it.

    >>> humanize_view("view_front_right_34.png")
    'Front Right ¾'
    >>> humanize_view("t=2.5s")
    't = 2.5 s'
    >>> humanize_view("pose_door_hinge@upper")
    'Pose · door_hinge@upper'
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    stem = raw
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem.lower().startswith("view_"):
        stem = stem[5:]
    low = stem.lower()
    if low in _WHOLE:
        return _WHOLE[low]
    if low.startswith("t=") and low.endswith("s"):
        return f"t = {stem[2:-1]} s"
    if low.startswith("pose_"):
        return f"Pose · {stem[5:]}"
    if low.startswith("frame_"):
        return f"Frame {stem[6:]}"
    return " ".join(_word(t) for t in stem.split("_") if t) or stem


def verdict_label(verdict: str) -> str:
    return VERDICT_META.get(verdict, (verdict, "", ""))[0]


def verdict_class(verdict: str) -> str:
    return VERDICT_META.get(verdict, ("", "", ""))[1]


def verdict_title(verdict: str) -> str:
    return VERDICT_META.get(verdict, ("", "", ""))[2]
