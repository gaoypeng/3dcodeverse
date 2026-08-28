"""Render-vs-reference DIFF: the measured IoU plus a vision call that names
concrete, fixable mismatches (missing feature, wrong part count, wrong
proportion) instead of a vague score.

Two consumers:

* :class:`codeverse.judges.reference.ReferenceJudge` puts ``ReferenceDiff.as_text()``
  in front of the scoring call, so the judge's own issues and improvement plan
  quote the named mismatches (and the refine loop inherits them through the
  normal ``judgment.improvement_plan`` path).
* :func:`refine_tasks` turns the top-3 mismatches into deterministic priority-1
  refine tasks for a track that wants them without going through the judge.

Never raises: a failed diff degrades to ``ReferenceDiff(error=…)`` with no
mismatches, and everything downstream behaves as if there were none.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.spec import Spec
from codeverse.models.base import ModelError
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.reference.prompts import DIFF_SYSTEM, DIFF_USER
from codeverse.reference.spec_text import brief_text
from codeverse.reference.types import Mismatch, ReferenceDiff

log = logging.getLogger(__name__)

MAX_MISMATCHES = 6
#: how many mismatches become refine tasks
TOP_TASKS = 3
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
    req = ChatRequest(messages=[ChatMessage.user(text, images=images)], system=DIFF_SYSTEM,
                      response_schema=DiffAnswer.model_json_schema(), temperature=temperature,
                      thinking="low", max_output_tokens=65_536, max_wait_s=240.0, label="reference_diff")
    try:
        resp = model.generate(req)
    except ModelError as e:
        log.warning("reference diff failed: %s", e)
        diff.error = f"diff call failed: {e}"
        return diff
    diff.usage = resp.usage
    payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
    try:
        ans = DiffAnswer.model_validate(payload)
    except (ValidationError, TypeError) as e:
        log.warning("reference diff unparsable: %s", e)
        diff.error = f"diff answer unparsable: {e}"
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


def refine_tasks(diff: ReferenceDiff, *, top: int = TOP_TASKS) -> list[Any]:
    """The top mismatches as priority-1 ``RefineTask``s (``source='gate'`` so the
    round policy protects them from being trimmed as judge chatter)."""
    from codeverse.orchestrator.rounds import RefineTask

    out = []
    for m in diff.top(top):
        out.append(RefineTask(
            target=m.target or "overall",
            kind="reference",
            instruction=(f"Reference mismatch ({m.kind.replace('_', ' ')}, {m.severity}): {m.detail} "
                         f"Look at the reference image again and fix this specifically."),
            priority=1,
            source="gate",
        ))
    return out


__all__ = ["DiffAnswer", "MAX_MISMATCHES", "TOP_TASKS", "compare", "refine_tasks"]
