"""``VlmJudge``: rubric YAML + labelled renders + measurements → ``Judgment``.

Per sample: one ``ChatRequest`` with a per-rubric JSON schema (criteria scored
0..1 with evidence, the rubric's binary defect checklist, issues, improvement
plan, acceptance verdicts); samples differ by montage/tile order (shuffle seed)
so n-sample mean/std measures judge noise.  With ``n_samples > 1`` the model
calls run in parallel (``codeverse.fanout``); results accumulate in sample order.  Score/defect penalties/floors/caps
/pass are computed in code (``scoring.py``).  Images are ≤2×2 montages
(``montage.py``); tracks may pass a clay/normals ``geometry_views`` RenderSet.  Retries: up to
``max_attempts`` per sample on ``ModelError`` / parse failure; if no sample
succeeds, a *degraded* Judgment (``passed=False, overall=0, summary
'judge_error: …'``, ``raw.status='degraded'``) is returned for the orchestrator
to treat as a glitch, never as a score.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from codeverse.config import get_settings
from codeverse.contracts.artifacts import RenderSet
from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Usage
from codeverse.contracts.judgment import Judgment
from codeverse.fanout import fan_out
from codeverse.judges.base import JudgeInput
from codeverse.judges.output_schema import (
    JudgeOutput,
    JudgeParseError,
    parse_judge_output,
    wire_schema,
)
from codeverse.judges.prompt_builder import build_judge_messages
from codeverse.judges.rubrics import Rubric, load_rubric
from codeverse.judges.scoring import aggregate_samples, degraded_judgment
from codeverse.models.base import ChatModel, ModelError

log = logging.getLogger(__name__)


@dataclass
class JudgeContext:
    """Per-call extras a subclass may supply (see ``VlmJudge.context``)."""

    measured_scores: dict[str, float] = field(default_factory=dict)
    extra_text: str = ""
    extra_images: list[tuple[str, str]] = field(default_factory=list)


class VlmJudge:
    """Rubric judge over a ``ChatModel``.  Thread-safe (stateless between calls)."""

    name = "vlm"

    def __init__(
        self,
        rubric: str | Rubric = "static_object_v1",
        model_id: str | None = None,
        n_samples: int = 1,
        temperature: float = 0.2,
        *,
        thinking: Literal["off", "low", "medium", "high"] = "low",
        max_attempts: int = 3,
        max_montages: int | None = None,
        detail_crops: int | None = None,
        max_px: int | None = None,
        chat_model: ChatModel | None = None,
        cache_dir: Path | None = None,
        label: str = "judge",
    ):
        self.rubric: Rubric = rubric if isinstance(rubric, Rubric) else load_rubric(rubric)
        self.model_id = model_id or get_settings().default_judge
        self.n_samples = max(1, int(n_samples))
        self.temperature = temperature
        self.thinking = thinking
        self.max_attempts = max(1, int(max_attempts))
        # payload size: the profile's dial (Settings.judge) unless the caller states one
        jd = get_settings().judge
        self.max_montages = max(1, int(jd.montages if max_montages is None else max_montages))
        self.detail_crops = max(0, int(jd.detail_crops if detail_crops is None else detail_crops))
        self.max_px = int(jd.max_px if max_px is None else max_px)
        self._model = chat_model
        self.cache_dir = cache_dir
        self.label = label

    # ------------------------------------------------------------------ model
    @property
    def model(self) -> ChatModel:
        if self._model is None:
            from codeverse.models import get_chat_model

            self._model = get_chat_model(self.model_id)
        return self._model

    # ------------------------------------------------------------------ API
    def judge(self, inp: JudgeInput, *, geometry_views: RenderSet | None = None) -> Judgment:
        """Judge one round.

        ``geometry_views``: optional clay/normals RenderSet for the geometry montage
        (falls back to ``inp.geometry_views`` if the input model carries that field;
        clay/normals views embedded in ``inp.renders`` by ``RenderView.mode`` are
        always routed to the geometry montage).
        """
        geometry_views = geometry_views or getattr(inp, "geometry_views", None)
        acceptance_ids = [a.id for a in inp.acceptance]
        schema = wire_schema(self.rubric, acceptance_ids)
        ctx = self.context(inp)
        measured, extra_text, extra_images = ctx.measured_scores, ctx.extra_text, ctx.extra_images
        usage = Usage()
        samples: list[JudgeOutput] = []
        errors: list[str] = []
        reqs: list[ChatRequest] = []
        for k in range(self.n_samples):
            seed = None if (self.n_samples == 1 and k == 0) else (inp.round_index * 1000 + k)
            # a missing render is a pipeline bug, not a judge glitch → JudgeImageError propagates
            system, messages = build_judge_messages(
                inp, self.rubric, shuffle_seed=seed, geometry_views=geometry_views, max_montages=self.max_montages,
                detail_crops=self.detail_crops, max_px=self.max_px, cache_dir=self.cache_dir,
                extra_images=extra_images, extra_text=extra_text,
            )
            reqs.append(ChatRequest(
                messages=messages, system=system, response_schema=schema, temperature=self.temperature,
                thinking=self.thinking,
                # the label is the ledger's attribution when the sample runs in a worker
                # thread (fan_out does not carry context vars): keep stage + round in it
                label=f"{self.label}:{self.rubric.name}:r{inp.round_index:02d}:s{k}",
            ))
        if len(reqs) == 1:  # serial path: no pool, no thread hop
            outcomes = [self._sample(reqs[0], acceptance_ids, measured)]
        else:
            self.model  # noqa: B018 — materialise the lazy chat model once, before the threads race
            results = fan_out(
                reqs, lambda r: self._sample(r, acceptance_ids, measured),
                max_workers=len(reqs), label=f"{self.label}:samples", item_name=lambda r: r.label,
            )
            # ordered accumulation; _sample never raises, but a crashed worker still counts as an error
            outcomes = [
                r if not isinstance(r, Exception) else (None, Usage(), f"{type(r).__name__}: {r}")
                for r in results
            ]
        for out, u, err in outcomes:
            usage = usage + u
            if out is not None:
                samples.append(out)
            else:
                errors.append(err)
        if not samples:
            return degraded_judgment(
                self.rubric, " || ".join(errors)[:2000], usage=usage, judge_backend=self.model_id, n_requested=self.n_samples
            )
        return aggregate_samples(
            self.rubric, samples, gates=inp.gates, acceptance_items=inp.acceptance,
            console_errors=inp.renders.console_errors, views=list(inp.renders.views), usage=usage,
            judge_backend=self.model_id, n_requested=self.n_samples, sample_errors=errors,
        )

    # ------------------------------------------------------------------ hook (ReferenceJudge overrides)
    def context(self, inp: JudgeInput) -> JudgeContext:
        """Per-call extras: measured-criterion scores (code), extra text and extra images.

        The base judge computes no measured criteria, so a rubric that declares
        some cannot be used with it (use ``ReferenceJudge`` or a subclass).
        """
        if self.rubric.measured_criteria():
            raise ValueError(
                f"rubric {self.rubric.name} has measured criteria "
                f"{[c.id for c in self.rubric.measured_criteria()]} but VlmJudge computes none; use a specialised judge"
            )
        return JudgeContext()

    # ------------------------------------------------------------------ one sample with retries
    def _sample(
        self, req: ChatRequest, acceptance_ids: list[str], measured: dict[str, float]
    ) -> tuple[JudgeOutput | None, Usage, str]:
        usage = Usage()
        last = ""
        for attempt in range(1, self.max_attempts + 1):
            t0 = time.time()
            try:
                resp: ChatResponse = self.model.generate(req)
            except ModelError as e:
                last = f"ModelError(attempt {attempt}): {e}"
                log.warning("judge %s: %s", req.label, last)
                if not e.retryable and attempt >= 2:
                    break
                continue
            usage = usage + resp.usage
            payload = resp.parsed if resp.parsed is not None else resp.text
            try:
                out = parse_judge_output(payload, self.rubric, acceptance_ids, measured_scores=measured)
                log.debug("judge %s ok in %.1fs", req.label, time.time() - t0)
                return out, usage, ""
            except JudgeParseError as e:
                last = f"parse(attempt {attempt}): {e}"
                log.warning("judge %s: %s", req.label, last[:300])
                req = req.model_copy(update={"temperature": min(1.0, self.temperature + 0.1 * attempt)})
        return None, usage, last or "unknown judge failure"
