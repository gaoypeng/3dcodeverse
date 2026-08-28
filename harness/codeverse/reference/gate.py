"""PLAUSIBILITY GATE — the thing that makes a synthesized reference safe to use.

One vision call per candidate image asks whether it really shows the requested
object, alone, on a plain background, without text/watermark/collage, and
without contradicting the spec's explicit visual constraints.  The verdict is
decided IN CODE from those booleans (:func:`decide`), never taken from the
model's own "ok" — the model only reports observations.

A candidate that fails is **discarded**: the caller falls back to no reference
at all.  Every verdict (pass or fail) is recorded on the ``ReferenceSet``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Usage
from codeverse.contracts.spec import Spec
from codeverse.models.base import ModelError
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.reference.prompts import GATE_SYSTEM, GATE_USER
from codeverse.reference.spec_text import brief_text, visual_constraints
from codeverse.reference.types import PlausibilityVerdict

log = logging.getLogger(__name__)


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
    return PlausibilityVerdict(
        ok=ok,
        shows_requested_object=ans.shows_requested_object,
        single_object=ans.single_object,
        plain_background=ans.plain_background,
        no_text_or_watermark=ans.no_text_or_watermark,
        is_photo_collage=ans.is_photo_collage,
        depicted_object=ans.depicted_object.strip()[:80],
        contradictions=[c.strip()[:200] for c in ans.contradictions if c.strip()][:6],
        reason=ans.reason.strip()[:300],
        model_id=model_id,
    )


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
    req = ChatRequest(
        messages=[ChatMessage.user(text, images=[ImagePart(path=str(path), label="CANDIDATE REFERENCE")])],
        system=GATE_SYSTEM,
        response_schema=GateAnswer.model_json_schema(),
        temperature=temperature,
        thinking="low",
        max_output_tokens=65_536,
        max_wait_s=240.0,
        label="reference_gate",
    )
    try:
        resp = model.generate(req)
    except ModelError as e:
        log.warning("reference gate: model error on %s: %s", path.name, e)
        return PlausibilityVerdict(reason=f"gate call failed: {e}", model_id=model_id), Usage()
    payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
    try:
        ans = GateAnswer.model_validate(payload)
    except (ValidationError, TypeError) as e:
        log.warning("reference gate: unparsable answer for %s: %s", path.name, e)
        return PlausibilityVerdict(reason=f"gate answer unparsable: {e}", model_id=model_id), resp.usage
    return decide(ans, model_id=model_id or getattr(model, "id", "")), resp.usage


__all__ = ["GateAnswer", "check_plausible", "decide"]
