"""``PairwiseJudge``: which of two candidates better satisfies the spec?

Position bias is real, so the comparison runs TWICE with A/B swapped (the two
orderings run in parallel via ``codeverse3d.proc``); the winner is declared
only when both orderings agree, otherwise ``tie``.  Each call sees the brief,
the rubric's criteria (titles + descriptions) and ONE 2×2 montage per candidate
(the 4 most informative views — never a big sheet: VLM judges flip with many
tiles).
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart, TextPart
from codeverse3d.contracts.common import Track, Usage
from codeverse3d.contracts.spec import Spec
from codeverse3d.judges.base import judged_subset
from codeverse3d.judges.prompt_builder import (
    MONTAGE_TILE_PX,
    brief_section,
    image_part,
    montage_label,
    plan_montages,
    prepare_image,
    render_montage,
)
from codeverse3d.judges.rubrics import JudgeParseError, Rubric, load_rubric
from codeverse3d.models.base import ChatModel, ModelError
from codeverse3d.models.schema_utils import JsonParseError, parse_json_lenient
from codeverse3d.proc import fan_out

log = logging.getLogger(__name__)

Winner = Literal["a", "b", "tie"]


class CriterionWinner(BaseModel):
    criterion: str
    winner: Literal["A", "B", "tie"]


class PairwiseReply(BaseModel):
    """Wire schema for one ordering (lists, not dicts: provider schemas dislike free-form keys)."""

    winner: Literal["A", "B", "tie"] = Field(description="which candidate better satisfies the brief overall")
    confidence: float = Field(ge=0.0, le=1.0, description="0.5 = coin flip, 1.0 = certain")
    reasons: list[str] = Field(description="2-5 short, evidence-based reasons citing candidate and view")
    criteria_won: list[CriterionWinner] = Field(default_factory=list, description="one entry per rubric criterion")


class PairwiseResult(BaseModel):
    winner: Winner
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    orderings: list[dict] = Field(default_factory=list, description="per-ordering raw verdicts (mapped to a/b)")
    usage: Usage = Field(default_factory=Usage)
    error: str = ""


_SYSTEM = """You are a BLIND comparative judge for a 3D-code harness. You see a brief, a rubric and renders of TWO candidates, labelled A and B. Decide which candidate better satisfies the brief, judging ONLY what is visible. Position carries no information: A is not better for being first.
Compare criterion by criterion (intent, structure, detail, proportions, fit, materials, cleanliness as listed), then decide overall. Prefer the candidate with no major defect over the one with more detail but a floating or broken part. Say 'tie' only when the two are genuinely equivalent. Reply with one JSON object: winner ('A'|'B'|'tie'), confidence 0..1, reasons[], criteria_won[{criterion, winner}]."""


class PairwiseJudge:
    name = "pairwise"

    def __init__(
        self,
        model_id: str | None = None,
        *,
        temperature: float = 0.2,
        max_px: int = 1024,
        views_per_side: int = 4,
        chat_model: ChatModel | None = None,
        cache_dir: Path | None = None,
    ):
        self.model_id = model_id or get_settings().default_judge
        self.temperature = temperature
        self.max_px = max(max_px, 1024)  # a 2×2 montage needs the resolution: 1024 is the floor, not a hint
        self.views_per_side = min(4, views_per_side)
        self._model = chat_model
        self.cache_dir = cache_dir

    @property
    def model(self) -> ChatModel:
        if self._model is None:
            from codeverse3d.models import get_chat_model

            self._model = get_chat_model(self.model_id)
        return self._model

    # ------------------------------------------------------------------ API
    def compare(
        self, spec: Spec, renders_a: RenderSet, renders_b: RenderSet, *,
        rubric: str | Rubric = "static_object_v1"
    ) -> PairwiseResult:
        """The two orderings' requests are tagged ``pairwise:fwd`` / ``pairwise:swap``
        so logs — and deterministic fakes — can tell concurrent orderings apart."""
        rub = rubric if isinstance(rubric, Rubric) else load_rubric(rubric)
        usage = Usage()
        verdicts: list[tuple[Winner, float, list[str]]] = []
        orderings: list[dict] = []
        errors: list[str] = []
        self.model  # noqa: B018 — materialise the lazy chat model once, before the threads race
        results = fan_out(
            (False, True), lambda swapped: self._ordering(spec, rub, renders_a, renders_b, swapped),
            max_workers=2, label="pairwise:orderings", item_name=lambda s: f"swapped={s}",
        )
        for res in results:  # ordered accumulation: (fwd, swap)
            if isinstance(res, Exception):
                raise res  # ModelError / parse failures are captured; anything else is a bug
            u, verdict, ordering, error = res
            usage = usage + u
            if error:
                errors.append(error)
                continue
            assert verdict is not None and ordering is not None
            verdicts.append(verdict)
            orderings.append(ordering)
        if not verdicts:
            return PairwiseResult(winner="tie", confidence=0.0, usage=usage, error=" || ".join(errors))
        return _combine(verdicts, orderings, usage, errors)

    def _ordering(
        self, spec: Spec, rub: Rubric, renders_a: RenderSet, renders_b: RenderSet, swapped: bool
    ) -> tuple[Usage, tuple[Winner, float, list[str]] | None, dict | None, str]:
        """One A/B ordering → ``(usage, verdict, ordering, error)`` (never raises for
        model/parse failures — the caller aggregates them as errors)."""
        first, second = (renders_b, renders_a) if swapped else (renders_a, renders_b)
        req = self._request(spec, rub, first, second, label="pairwise:swap" if swapped else "pairwise:fwd")
        try:
            resp = self.model.generate(req)
        except ModelError as e:
            return getattr(e, "usage", None) or Usage(), None, None, f"ModelError: {e}"
        try:
            payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
            reply = PairwiseReply.model_validate(payload)
        except JsonParseError as e:
            return resp.usage, None, None, f"parse: no JSON object in reply: {e}"
        except (JudgeParseError, ValueError) as e:
            return resp.usage, None, None, f"parse: {e}"
        winner = _map_winner(reply.winner, swapped)
        reasons = [_unswap_text(r, swapped) for r in reply.reasons]
        ordering = {"swapped": swapped, "winner": winner, "confidence": reply.confidence,
                    "reasons": reasons,
                    "criteria_won": {c.criterion: _map_winner(c.winner, swapped) for c in reply.criteria_won}}
        return resp.usage, (winner, reply.confidence, reasons), ordering, ""

    # ------------------------------------------------------------------ prompt
    def _request(
        self, spec: Spec, rub: Rubric, first: RenderSet, second: RenderSet, *, label: str = "pairwise"
    ) -> ChatRequest:
        crit = "\n".join(f"- {c.id}: {c.description.strip()}" for c in rub.visual_criteria())
        text = (
            brief_section(spec)
            + "\n\nCRITERIA (from rubric " + rub.name + "):\n" + crit
            + "\n\nImages follow: CANDIDATE A first, then CANDIDATE B. Each image is labelled."
        )
        parts: list[TextPart | ImagePart] = [TextPart(text=text)]
        for tag, rs in (("A", first), ("B", second)):
            for lbl, ip in self._side_images(tag, rs, scene=spec.track is Track.SCENE):
                parts.append(TextPart(text=lbl))
                parts.append(ip)
        parts.append(TextPart(text="Compare A and B and return the JSON object."))
        return ChatRequest(
            messages=[ChatMessage(role="user", parts=parts)], system=_SYSTEM,
            response_schema=PairwiseReply.model_json_schema(), temperature=self.temperature,
            max_wait_s=900.0, label=label,
        )

    def _side_images(self, tag: str, rs: RenderSet, *, scene: bool) -> list[tuple[str, ImagePart]]:
        """One 2×2 montage of the candidate's most informative views (plus its pose sheet / geometry
        montage if any), from the views the verdict judge saw, ranked for the track: a scene's
        own cameras first, as the verdict judge ranks them."""
        montages = plan_montages(judged_subset(rs), scene=scene, max_montages=2, detail_crops=0)
        out: list[tuple[str, ImagePart]] = []
        for i, m in enumerate(montages, 1):
            if len(m.tiles) > self.views_per_side:
                m = replace(m, tiles=m.tiles[: self.views_per_side])
            lbl = f"CANDIDATE {tag} — " + montage_label(m, i, len(montages))
            strip = f"CANDIDATE {tag} — {m.title.split(' (')[0]}"
            png = render_montage(m, cache_dir=self.cache_dir, tile_px=MONTAGE_TILE_PX)
            out.append((lbl, image_part(prepare_image(png, label=strip, max_px=self.max_px, cache_dir=self.cache_dir), lbl)))
        return out


_CAND = re.compile(r"\b([Cc]andidate)\s+([AB])\b")


def _unswap_text(text: str, swapped: bool) -> str:
    """Rewrite 'Candidate A/B' mentions from a swapped ordering back to the caller's a/b labels."""
    if not swapped:
        return text
    return _CAND.sub(lambda m: f"{m.group(1)} {'B' if m.group(2) == 'A' else 'A'}", text)


def _map_winner(w: str, swapped: bool) -> Winner:
    if w == "tie":
        return "tie"
    if not swapped:
        return "a" if w == "A" else "b"
    return "b" if w == "A" else "a"


def _combine(
    verdicts: list[tuple[Winner, float, list[str]]], orderings: list[dict], usage: Usage, errors: list[str]
) -> PairwiseResult:
    winners = {v[0] for v in verdicts}
    reasons = [r for v in verdicts for r in v[2]][:8]
    conf = statistics.fmean(v[1] for v in verdicts)
    if len(verdicts) == 1:  # only one ordering succeeded → downgrade confidence, keep winner
        return PairwiseResult(winner=verdicts[0][0], confidence=round(conf * 0.5, 3), reasons=reasons,
                              orderings=orderings, usage=usage, error=" || ".join(errors))
    if len(winners) == 1:
        return PairwiseResult(winner=verdicts[0][0], confidence=round(conf, 3), reasons=reasons, orderings=orderings, usage=usage)
    # disagreement (position bias or genuine ambiguity) → tie, low confidence
    return PairwiseResult(winner="tie", confidence=round(min(0.4, 1.0 - conf), 3), reasons=reasons, orderings=orderings,
                          usage=usage, error="orderings disagree")
