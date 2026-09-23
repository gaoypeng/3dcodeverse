"""Reference grounding: give the pipeline a picture of what it is building.

Today a prompt goes to a planner that has never SEEN the object.  This module
synthesizes a neutral studio product shot of the brief with the image model,
**validates** it (``check_plausible`` + ``decide``), and hands it to the rest of the harness as the
fidelity anchor: the planner sees it, the generator's ``compare_reference`` tool
sees it, the silhouette gate measures against it and the judge scores against it
(:class:`codeverse3d.judges.vlm_judge.ReferenceJudge`).

A synthesized reference is never ground truth and is marked as such everywhere
(``attach`` / ``SYNTH_NOTE``): the user's own ``--image`` wins, the brief's dimensions win,
and a picture that fails the gate is discarded rather than chased.

Entry points::

    from codeverse3d.reference import synth_reference, attach, compare

    refset = synth_reference(spec, model=chat, image_model=img, n_views=2)
    spec, why = attach(spec, refset)

(Collapsed from the 11-file ``codeverse3d/reference/`` package on 2026-08-28: its
entire external surface was two call sites — ``cli/main.ground_spec`` and the
judge's lazy ``compare``/``proportions`` imports.)
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from codeverse3d.config import get_settings
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.spec import ReferenceImage, Spec
from codeverse3d.models.schema_utils import ask_structured
from codeverse3d.proc import write_text_atomic
from codeverse3d.prompts import prompt_hash

log = logging.getLogger(__name__)


# ===================================================================== types
#: The note stamped on every synthesized ``ReferenceImage``.  It travels into the
#: planner prompt label, the generator's reference note, the judge's image label
#: and ``spec.json`` — so nothing downstream can mistake it for ground truth.
SYNTH_NOTE = (
    "SYNTHESIZED reference (AI-generated product shot of the brief, NOT a photo of a real object "
    "and NOT ground truth) — use it for shape, part inventory and proportions; the written brief "
    "and its stated dimensions always win where they disagree"
)

#: ``spec.tags`` marker so galleries / dataset meta can filter synthesized-reference runs.
SYNTH_TAG = "synthetic-reference"

ViewKind = Literal["three_quarter", "front", "side", "back", "detail"]

MismatchKind = Literal[
    "missing_feature", "extra_feature", "wrong_part_count", "wrong_proportion",
    "wrong_shape", "wrong_material", "wrong_placement",
]


class GateAnswer(BaseModel):
    """What the vision model reports (observations only — no verdict)."""

    depicted_object: str = ""
    shows_requested_object: bool = False
    single_object: bool = False
    plain_background: bool = False
    no_text_or_watermark: bool = False
    is_photo_collage: bool = False
    contradictions: list[str] = Field(default_factory=list)
    reason: str = ""


class PlausibilityVerdict(GateAnswer):
    """One vision call's answer about ONE candidate reference image: the model's
    observations (``depicted_object`` — what it says the image shows; ``contradictions`` —
    the explicit spec constraints the image contradicts) plus the verdict.

    ``ok`` is computed in code from the booleans + ``contradictions`` (see
    :func:`decide`), never taken from the model.
    """

    ok: bool = False
    model_id: str = ""

    def failure(self) -> str:
        """One-line why-rejected (empty when ``ok``)."""
        if self.ok:
            return ""
        bits: list[str] = []
        if not self.shows_requested_object:
            bits.append(f"shows {self.depicted_object or 'something else'}")
        if not self.single_object:
            bits.append("not a single object")
        if not self.plain_background:
            bits.append("background not plain")
        if self.is_photo_collage:
            bits.append("collage/multi-panel")
        if not self.no_text_or_watermark:
            bits.append("text or watermark")
        bits += [f"contradicts: {c}" for c in self.contradictions]
        return "; ".join(bits) or (self.reason or "rejected")


class ReferenceView(BaseModel):
    """One synthesized image plus the verdict that let it in (or kept it out)."""

    path: str
    view: ViewKind = "three_quarter"
    image_prompt: str = ""
    accepted: bool = False
    verdict: PlausibilityVerdict | None = None
    aspect: float | None = Field(default=None, description="width/height of the image's own silhouette")
    dimension_conflict: bool = Field(
        default=False,
        description="the image's proportions contradict the brief's stated dimensions; the BRIEF wins and "
                    "no numeric proportion signal from this picture may be used (reference.proportions)")


class ReferenceSet(BaseModel):
    """The result of :func:`synth_reference`.

    ``accepted`` is what may be attached to a spec; ``rejected`` is kept only so
    the run can record *why* the fallback to no-reference happened.
    """

    prompt: str
    key: str = Field(default="", description="cache key (prompt + constraints + models + template hash)")
    synthesized: bool = True
    object_name: str = ""
    subject: str = Field(default="", description="the described target the image prompts were built from")
    text_model: str = ""
    image_model: str = ""
    views: list[ReferenceView] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    source: Literal["fresh", "cache", "none"] = "none"
    error: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def accepted(self) -> list[ReferenceView]:
        return [v for v in self.views if v.accepted]

    @property
    def rejected(self) -> list[ReferenceView]:
        return [v for v in self.views if not v.accepted]

    @property
    def ok(self) -> bool:
        return bool(self.accepted)

    def summary(self) -> str:
        if self.error:
            return f"reference synthesis failed: {self.error}"
        good, bad = len(self.accepted), len(self.rejected)
        head = f"{good} accepted / {good + bad} generated ({self.source}, {self.image_model or 'no image model'})"
        flagged = [v.view for v in self.accepted if v.dimension_conflict]
        if flagged:
            head += (f" — proportions of {', '.join(flagged)} disagree with the brief's dimensions "
                     "(shape target only; the brief's numbers win)")
        if bad:
            head += " — rejected: " + "; ".join(f"{v.view}: {v.verdict.failure() if v.verdict else 'no verdict'}"
                                                for v in self.rejected)
        return head


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
    synthesized: bool = False
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
        lines = ["REFERENCE DIFF (harness vision pass, target = the REFERENCE image"
                 + (", SYNTHESIZED — not ground truth" if self.synthesized else "") + "):"]
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


# ===================================================================== prompts
# --------------------------------------------------------------------------- image prompt writer
IMAGE_PROMPT_SYSTEM = (
    "You write prompts for a text-to-image model.  You are given a 3D modelling brief.  Your job is to "
    "describe THE SAME OBJECT as a neutral studio product photograph, so a 3D modeller can use the picture "
    "as a shape target.  Describe only what a camera would see: the object's parts, their proportions and "
    "their materials.  Never invent brand names, never add props, people, hands, scale figures or scenery. "
    "Answer with JSON only."
)

IMAGE_PROMPT_USER = """\
3D MODELLING BRIEF
------------------
{brief}

Write ONE `subject` sentence (25-60 words) describing this object as it would look in a product catalogue:
its overall form, its named parts, their relative sizes, and the material of each part.  Keep every explicit
requirement of the brief (part counts, features, materials).  Do not mention the camera, the background or
the lighting — the harness adds those.

Then write `object_name`: 1-4 words naming the object (e.g. "hand-crank coffee grinder").

Then, for each of the {n_views} requested views {views}, write a `view_note`: at most 12 words saying what
that camera angle must make readable (e.g. "front face, crank handle silhouette, drawer front").
"""

#: Harness-owned camera/background clause appended to every image prompt — this is
#: what makes the picture usable as a silhouette target (plain backdrop, whole object
#: in frame, no crop, no styling).
STUDIO_SUFFIX = (
    "neutral studio product photograph of a single object, isolated on a plain seamless light grey "
    "background, the complete object fully inside the frame with a small even margin on all sides, "
    "nothing cropped, standing upright in its natural resting orientation, soft even studio lighting, "
    "no cast shadow on the backdrop, sharp focus throughout, no depth of field, no props, no hands, "
    "no people, no scale figures, no scenery, no text, no labels, no logos, no watermark, no border, "
    "no collage, no multiple views in one image, single frame only, photorealistic, colour photograph"
)

#: view name → the camera clause the harness owns for it
VIEW_CLAUSE: dict[str, str] = {
    "three_quarter": "three-quarter view from slightly above, front and one side both visible",
    "front": "straight-on front elevation, camera at object height, no perspective distortion",
    "side": "straight-on side elevation, camera at object height, no perspective distortion",
    "back": "straight-on rear elevation, camera at object height",
    "detail": "close view of the most characteristic feature, whole feature in frame",
}

#: order views are requested in as ``n_views`` grows.  The straight-on ``front``
#: elevation comes second on purpose: it is the SILHOUETTE TARGET, and the harness
#: measures IoU against the run's ``front`` render (``tracks.static_object.silhouette_gate``),
#: so the two cameras must agree.  The 3/4 shot carries the part inventory.
VIEW_ORDER: tuple[str, ...] = ("three_quarter", "front", "side", "back")


# --------------------------------------------------------------------------- plausibility gate
GATE_SYSTEM = (
    "You verify whether a generated image can be used as a modelling reference.  You are strict and "
    "literal: you report what the image actually shows, not what it was meant to show.  Answer with JSON only."
)

GATE_USER = """\
The image was generated as a reference photo for this 3D modelling brief.

BRIEF
-----
{brief}

EXPLICIT CONSTRAINTS THE IMAGE MUST NOT CONTRADICT
--------------------------------------------------
{constraints}

Answer these questions about the IMAGE:
- `depicted_object`: 1-6 words naming what the image actually shows.
- `shows_requested_object`: true only if the depicted object IS the object the brief asks for
  (a different object, or an unrecognisable blob, is false).
- `single_object`: true only if exactly ONE object is shown (a base/stand/tray that the brief itself
  asks for counts as part of the object; a second copy of the object, extra props or accessories do not).
- `plain_background`: true only if the background is a plain, empty, untextured backdrop.
- `no_text_or_watermark`: true only if there is NO text, caption, label, dimension line, logo or watermark.
- `is_photo_collage`: true if the frame contains multiple panels, multiple views, a grid or a turnaround sheet.
- `contradictions`: for EACH constraint above that the image visibly contradicts, one short sentence naming
  the constraint and what the image shows instead.  Empty list if none.  Only list what you can SEE — do not
  guess at absolute dimensions from a photo with no scale reference.
- `reason`: one sentence summarising your verdict.
"""


# --------------------------------------------------------------------------- reference diff
DIFF_SYSTEM = (
    "You compare a 3D render against a reference image and name concrete, fixable differences. "
    "You never comment on background, lighting, camera framing or image style — only on the object's "
    "shape, its parts, their counts, their proportions, their placement and their materials. "
    "Answer with JSON only."
)

DIFF_USER = """\
The images labelled REFERENCE are the shape target.  The images labelled RENDER are the current 3D model.
{synth_note}

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


# ===================================================================== cache

CACHE_SUBDIR = "references"


def template_hash() -> str:
    """One hash over every prompt constant that shapes a reference image."""
    blob = "\x00".join([
        IMAGE_PROMPT_SYSTEM, IMAGE_PROMPT_USER, STUDIO_SUFFIX, GATE_SYSTEM, GATE_USER,
        *(f"{k}={v}" for k, v in sorted(VIEW_CLAUSE.items())),
    ])
    return prompt_hash(blob)


def cache_key(spec: Spec, *, n_views: int, text_model: str, image_model: str) -> str:
    h = hashlib.sha256()
    for part in (template_hash(), brief_text(spec), str(n_views), text_model, image_model):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()[:20]


def cache_root(cache_dir: Path | None = None) -> Path:
    return (cache_dir or get_settings().cache_dir) / CACHE_SUBDIR


def load(key: str, *, cache_dir: Path | None = None) -> ReferenceSet | None:
    """Cached set whose image files all still exist, else ``None`` (self-healing)."""
    path = cache_root(cache_dir) / key / "set.json"
    if not path.is_file():
        return None
    try:
        rs = ReferenceSet.model_validate_json(path.read_text())
    except (ValidationError, ValueError) as e:
        log.warning("reference cache %s unreadable (%s); ignoring", key, e)
        return None
    if any(not Path(v.path).is_file() for v in rs.views):
        log.info("reference cache %s lost its images; regenerating", key)
        return None
    rs.source, rs.usage = "cache", Usage()
    return rs


def store(rs: ReferenceSet, *, cache_dir: Path | None = None) -> Path:
    return write_text_atomic(cache_root(cache_dir) / rs.key / "set.json", rs.model_dump_json(indent=2))


def image_dir(key: str, *, cache_dir: Path | None = None) -> Path:
    d = cache_root(cache_dir) / key
    d.mkdir(parents=True, exist_ok=True)
    return d


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


# ===================================================================== gate


def decide(ans: GateAnswer, *, model_id: str = "") -> PlausibilityVerdict:
    """Observations → verdict, in code.  ALL of the checks must hold."""
    ok = (
        ans.shows_requested_object
        and ans.single_object
        and ans.plain_background
        and ans.no_text_or_watermark
        and not ans.is_photo_collage
        and not ans.contradictions
    )
    return PlausibilityVerdict(**{
        **ans.model_dump(),
        "depicted_object": ans.depicted_object.strip()[:80],
        "contradictions": [c.strip()[:200] for c in ans.contradictions if c.strip()][:6],
        "reason": ans.reason.strip()[:300],
    }, ok=ok, model_id=model_id)


def check_plausible(
    image: Path | str,
    spec: Spec,
    *,
    model: Any,
    model_id: str = "",
    temperature: float = 0.0,
) -> tuple[PlausibilityVerdict, Usage]:
    """One vision call.  A model/parse failure is a REJECTION, never an exception:
    a reference we could not verify must not be used."""
    path = Path(image)
    if not path.is_file():
        return PlausibilityVerdict(reason=f"image missing: {path.name}", model_id=model_id), Usage()
    text = GATE_USER.format(brief=brief_text(spec), constraints=visual_constraints(spec))
    ans, usage, err = ask_structured(model, GateAnswer, system=GATE_SYSTEM, text=text,
                           images=[ImagePart(path=str(path), label="CANDIDATE REFERENCE")],
                           temperature=temperature, label="reference_gate")
    if err:
        log.warning("reference gate on %s: %s", path.name, err)
        return PlausibilityVerdict(reason=f"gate {err}", model_id=model_id), usage
    return decide(ans, model_id=model_id or getattr(model, "id", "")), usage


# ===================================================================== attach
def has_user_references(spec: Spec) -> bool:
    """True when the user supplied their own reference image(s) (``--image``)."""
    return any(not is_synthetic(r) for r in spec.references)


def is_synthetic(ref: ReferenceImage) -> bool:
    return ref.note.startswith("SYNTHESIZED")


#: view kinds that can serve as the silhouette ``target``, best first — the harness
#: measures IoU against a straight-on render, so a straight-on photo is the right
#: target and the three-quarter shot is only a detail/part-inventory image.
TARGET_PREFERENCE: tuple[str, ...] = ("front", "side", "back", "three_quarter")


def reference_images(refset: ReferenceSet) -> list[ReferenceImage]:
    """Accepted views as spec references, ``target`` first.

    Exactly one view is the ``target`` (the only one the silhouette gate and the
    measured ``silhouette_match`` criterion compare against); the rest are
    ``detail`` images the planner, generator and judge still see.
    """
    usable = [v for v in refset.accepted if v.path and Path(v.path).is_file()]
    if not usable:
        return []
    target = min(usable, key=lambda v: (TARGET_PREFERENCE.index(v.view)
                                        if v.view in TARGET_PREFERENCE else len(TARGET_PREFERENCE)))
    ordered = [target] + [v for v in usable if v is not target]
    return [ReferenceImage(path=str(Path(v.path).resolve()), role="target" if v is target else "detail",
                           note=f"{SYNTH_NOTE} [{v.view} view, {refset.image_model or 'image model'}]")
            for v in ordered]


def attach(spec: Spec, refset: ReferenceSet) -> tuple[Spec, str]:
    """``(spec, why)`` — the spec to run with, and one line saying what happened.

    The returned spec is a copy; ``spec`` itself is never mutated.
    """
    if has_user_references(spec):
        return spec, "user --image references win; synthesized reference not attached"
    refs = reference_images(refset)
    if not refs:
        return spec, f"no usable synthesized reference ({refset.summary()}) — running without one"
    tags = list(spec.tags) + ([SYNTH_TAG] if SYNTH_TAG not in spec.tags else [])
    return spec.model_copy(update={"references": refs, "tags": tags}), (
        f"attached {len(refs)} SYNTHESIZED reference image(s) ({refset.summary()})"
    )


# ===================================================================== synth

MAX_VIEWS = len(VIEW_ORDER)
DEFAULT_SIZE = 1024


class ViewPrompt(BaseModel):
    view: str
    view_note: str = ""


class ImagePromptPlan(BaseModel):
    object_name: str = ""
    subject: str = ""
    views: list[ViewPrompt] = Field(default_factory=list)


def views_for(n_views: int) -> list[str]:
    return list(VIEW_ORDER[: max(1, min(int(n_views), MAX_VIEWS))])


def compose_image_prompt(subject: str, view: str, note: str = "") -> str:
    """Harness-owned composition: subject + view clause + note + studio suffix."""
    subject = " ".join(subject.strip().rstrip(".,;").split())
    clause = VIEW_CLAUSE.get(view, VIEW_CLAUSE["three_quarter"])
    note = " ".join(note.strip().rstrip(".,;").split())
    bits = [subject, clause, *( [f"the picture must make readable: {note}"] if note else [] ), STUDIO_SUFFIX]
    return ", ".join(bits)


def _fallback_plan(spec: Spec, view_names: list[str]) -> ImagePromptPlan:
    """No text model (or it failed) → use the brief's own words as the subject."""
    return ImagePromptPlan(object_name=spec.prompt.strip()[:60],
                           subject=brief_text(spec).replace("\n", ", "),
                           views=[ViewPrompt(view=v) for v in view_names])


def image_prompt_plan(spec: Spec, view_names: list[str], *, model: Any, temperature: float = 0.3) -> tuple[ImagePromptPlan, Usage]:
    """ONE structured text call: brief → subject sentence + per-view notes."""
    if model is None:
        return _fallback_plan(spec, view_names), Usage()
    text = IMAGE_PROMPT_USER.format(brief=brief_text(spec), n_views=len(view_names), views=", ".join(view_names))
    plan, usage, err = ask_structured(model, ImagePromptPlan, system=IMAGE_PROMPT_SYSTEM, text=text,
                            temperature=temperature, label="reference_prompt")
    if err:
        log.warning("reference prompt writer %s; using the brief verbatim", err)
        return _fallback_plan(spec, view_names), usage
    if not plan.subject.strip():
        plan.subject = brief_text(spec).replace("\n", ", ")
    by_view = {v.view.strip().lower(): v for v in plan.views}
    plan.views = [by_view.get(v, ViewPrompt(view=v)) for v in view_names]
    for v, name in zip(plan.views, view_names, strict=True):
        v.view = name
    return plan, usage


def synth_reference(
    spec: Spec,
    *,
    model: Any = None,
    image_model: Any = None,
    n_views: int = 2,
    model_id: str = "",
    cache_dir: Path | None = None,
    use_cache: bool = True,
    out_dir: Path | None = None,
    size: int = DEFAULT_SIZE,
) -> ReferenceSet:
    """Synthesize + validate reference images for ``spec``.  Never raises.

    ``out_dir`` (a run's ``artifacts/reference``) receives a copy of every
    accepted image so the run is self-contained; the cache keeps the originals.
    """
    view_names = views_for(n_views)
    text_id = model_id or getattr(model, "id", "") or ""
    image_id = getattr(image_model, "id", "") or getattr(image_model, "model", "") or ""
    key = cache_key(spec, n_views=len(view_names), text_model=text_id, image_model=image_id)
    if use_cache:
        hit = load(key, cache_dir=cache_dir)
        if hit is not None:
            _refresh_conflicts(spec, hit)  # the proportion check may have changed since it was cached
            log.info("reference cache hit %s (%s)", key, hit.summary())
            return _publish(hit, out_dir)
    rs = ReferenceSet(prompt=spec.prompt, key=key, text_model=text_id, image_model=image_id, source="fresh")
    if image_model is None:
        rs.error = "no image model configured"
        return rs
    plan, usage = image_prompt_plan(spec, view_names, model=model)
    rs.usage, rs.object_name, rs.subject = usage, plan.object_name.strip()[:80], plan.subject.strip()
    out = image_dir(key, cache_dir=cache_dir)
    for i, vp in enumerate(plan.views):
        prompt = compose_image_prompt(plan.subject, vp.view, vp.view_note)
        view = ReferenceView(path="", view=vp.view, image_prompt=prompt)  # type: ignore[arg-type]
        try:
            images, u = image_model.generate_with_usage(prompt, size=size, n=1, seed=spec.seed + i)
        except Exception as e:  # noqa: BLE001 — a reference is optional; it must never fail the run
            log.warning("reference image %s failed: %s", vp.view, e)
            rs.error = rs.error or f"{type(e).__name__}: {e}"
            continue
        rs.usage = rs.usage + u
        if not images:
            continue
        path = out / f"ref_{i}_{vp.view}.png"
        images[0].save(path)
        view.path = str(path)
        verdict, gu = check_plausible(path, spec, model=model, model_id=text_id) if model is not None else (None, Usage())
        rs.usage = rs.usage + gu
        if verdict is not None:
            view.verdict, view.accepted = verdict, verdict.ok
            if verdict.ok:
                info = dimension_conflict(spec, path)
                view.aspect, view.dimension_conflict = info.get("image_aspect"), bool(info.get("conflict"))
        else:  # no text model to verify with → an unverified image is NOT used
            view.accepted = False
        rs.views.append(view)
    if use_cache and rs.views:
        store(rs, cache_dir=cache_dir)
    log.info("reference synthesis %s: %s", key, rs.summary())
    return _publish(rs, out_dir)


def _refresh_conflicts(spec: Spec, rs: ReferenceSet) -> None:
    """Recompute the proportion flags of a cached set against today's rules."""
    for v in rs.views:
        if not v.accepted or not v.path or not Path(v.path).is_file():
            continue
        info = dimension_conflict(spec, v.path)
        v.aspect, v.dimension_conflict = info.get("image_aspect"), bool(info.get("conflict"))


def _publish(rs: ReferenceSet, out_dir: Path | None) -> ReferenceSet:
    """Copy accepted images into the run and repoint their paths (idempotent).  The set
    itself is written by ``ground_spec``, which records the all-rejected case too."""
    if out_dir is None or not rs.accepted:
        return rs
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for v in rs.views:
        if not v.accepted or not v.path:
            continue
        src = Path(v.path)
        dst = out_dir / src.name
        if src.resolve() == dst.resolve():
            continue
        try:
            shutil.copy2(src, dst)
        except OSError as e:  # keep the cache path — still readable
            log.warning("could not copy reference into the run (%s); using the cache path", e)
            continue
        v.path = str(dst)
    return rs


# ===================================================================== mismatch

MAX_MISMATCHES = 6
_SYNTH_LINE = ("The REFERENCE was SYNTHESIZED from the brief by an image model — it is a shape target, "
               "not ground truth.  Where it disagrees with the brief, the BRIEF wins and it is not a mismatch.")


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
    synthesized: bool = False,
    temperature: float = 0.1,
    max_reference: int = 2,
    max_renders: int = 3,
) -> ReferenceDiff:
    """One vision call: REFERENCE images + RENDER images → named mismatches."""
    refs = [Path(p) for p in reference_images if Path(p).is_file()][:max_reference]
    rens = [Path(p) for p in render_images if Path(p).is_file()][:max_renders]
    diff = ReferenceDiff(synthesized=synthesized, model_id=getattr(model, "id", "") if model else "")
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
        synth_note=_SYNTH_LINE if synthesized else "",
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


# ===================================================================== run

ARTIFACT_SUBDIR = "reference"


def ground_spec(
    spec: Spec,
    ws: Any,
    *,
    n_views: int = 2,
    model: Any | None = None,
    image_model: Any | None = None,
    image_model_id: str = "",
    events: Any | None = None,
    cache_dir: Path | None = None,
) -> tuple[Spec, ReferenceSet, str]:
    """``(spec, refset, why)``.  ``spec`` is unchanged when no reference was attached."""
    empty = ReferenceSet(prompt=spec.prompt)
    if has_user_references(spec):
        why = "user --image references win; no reference synthesized"
        _emit(events, "reference.skipped", reason=why)
        return spec, empty, why
    try:
        model = model if model is not None else _chat_model(spec.backends.planner)
        image_model = image_model if image_model is not None else _image_model(image_model_id)
    except Exception as e:  # noqa: BLE001 — no keys / no package → run without a reference
        why = f"reference grounding unavailable: {type(e).__name__}: {e}"
        log.warning("%s", why)
        _emit(events, "reference.error", reason=why)
        return spec, empty, why
    out_dir = Path(ws.artifacts) / ARTIFACT_SUBDIR
    _emit(events, "reference.start", n_views=n_views, image_model=getattr(image_model, "id", ""))
    refset = synth_reference(spec, model=model, image_model=image_model, n_views=n_views,
                             model_id=spec.backends.planner, out_dir=out_dir, cache_dir=cache_dir)
    grounded, why = attach(spec, refset)
    if grounded is not spec:
        ws.write_json(ws.spec_path, grounded)
        ws.commit("reference")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reference_set.json").write_text(refset.model_dump_json(indent=2))
    _emit(events, "reference.done", accepted=len(refset.accepted), rejected=len(refset.rejected),
          source=refset.source, cost_usd=round(refset.usage.cost_usd, 4), why=why,
          rejected_reasons=[v.verdict.failure() if v.verdict else "" for v in refset.rejected])
    return grounded, refset, why


def _chat_model(model_id: str) -> Any:
    from codeverse3d.models import get_chat_model

    return get_chat_model(model_id)


def _image_model(model_id: str = "") -> Any:
    from codeverse3d.models.gemini import DEFAULT_IMAGE_MODEL, GeminiImageModel

    return GeminiImageModel(model_id or DEFAULT_IMAGE_MODEL)


def _emit(events: Any, name: str, **payload: Any) -> None:
    if events is None:
        return
    try:
        events.emit(name, **payload)
    except Exception:  # noqa: BLE001 - telemetry must never break a run
        log.debug("could not emit %s", name, exc_info=True)


__all__ = [
    "SYNTH_NOTE", "SYNTH_TAG", "Mismatch", "PlausibilityVerdict", "ReferenceDiff", "ReferenceSet",
    "ReferenceView", "attach", "check_plausible", "compare", "compose_image_prompt", "conflict_note",
    "decide", "dimension_conflict", "expected_aspect_range",
    "ground_spec", "has_user_references", "is_synthetic", "reference_images",
    "synth_reference", "views_for",
]
