"""Typed shapes for reference grounding: what a synthesized reference is, what
the plausibility gate decided, and what a render-vs-reference diff found.

Everything here is data-only (pydantic) so it can be written next to a run,
cached under ``~/.cache/codeverse/references`` and quoted in a record without
importing any model backend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from codeverse.contracts.common import Usage

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


class PlausibilityVerdict(BaseModel):
    """One vision call's answer about ONE candidate reference image.

    ``ok`` is computed in code from the booleans + ``contradictions`` (see
    :func:`codeverse.reference.gate.decide`), never taken from the model.
    """

    ok: bool = False
    shows_requested_object: bool = False
    single_object: bool = False
    plain_background: bool = False
    no_text_or_watermark: bool = False
    is_photo_collage: bool = False
    depicted_object: str = Field(default="", description="what the vision model says the image shows")
    contradictions: list[str] = Field(default_factory=list,
                                      description="explicit spec constraints the image contradicts")
    reason: str = ""
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
    """The result of :func:`codeverse.reference.synth.synth_reference`.

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
        for m in sorted(self.mismatches, key=lambda x: _SEVERITY_ORDER.get(x.severity, 3))[:n]:
            lines.append(f"- {m.as_line()}")
        if self.matches:
            lines.append("- already matching: " + "; ".join(self.matches[:4]))
        return "\n".join(lines)


__all__ = ["Mismatch", "MismatchKind", "PlausibilityVerdict", "ReferenceDiff", "ReferenceSet", "ReferenceView",
           "SYNTH_NOTE", "SYNTH_TAG", "ViewKind"]
