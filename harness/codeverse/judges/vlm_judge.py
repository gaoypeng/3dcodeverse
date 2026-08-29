"""``VlmJudge``: rubric YAML + labelled renders + measurements → ``Judgment``.

Per sample: one ``ChatRequest`` with a per-rubric JSON schema (criteria scored
0..1 with evidence, the rubric's binary defect checklist, issues, improvement
plan, acceptance verdicts); samples differ by montage/tile order (shuffle seed)
so n-sample mean/std measures judge noise.  With ``n_samples > 1`` the model
calls run in parallel (``codeverse.proc``); results accumulate in sample order.  Score/defect penalties/floors/caps
/pass are computed in code (``scoring.py``).  Images are ≤2×2 montages
(``montage.py``); tracks may pass a clay/normals ``geometry_views`` RenderSet.  Retries: up to
``max_attempts`` per sample on ``ModelError`` / parse failure, all of them inside one
``sample_budget_s`` (:data:`SAMPLE_BUDGET_S`) that also clips the model's own retry
deadline (``ChatRequest.max_wait_s``); if no sample
succeeds, a *degraded* Judgment (``passed=False, overall=0, summary
'judge_error: …'``, ``raw.status='degraded'``) is returned for the orchestrator
to treat as a glitch, never as a score.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from codeverse.config import get_settings
from codeverse.contracts.artifacts import Judgment, RenderSet, RenderView
from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Usage
from codeverse.judges.base import JudgeInput
from codeverse.judges.prompt_builder import build_judge_messages, judge_prompt_hash
from codeverse.judges.rubrics import (
    JudgeOutput,
    JudgeParseError,
    Rubric,
    aggregate_samples,
    degraded_judgment,
    load_rubric,
    parse_judge_output,
    wire_schema,
)
from codeverse.models.base import ChatModel, ModelError
from codeverse.proc import fan_out

log = logging.getLogger(__name__)

#: retry budget for ONE judge sample, every attempt included — the owner's rule
#: (2026-08-27): generous time, zero timeouts.  For scale, the verdict call is 42 s p50 /
#: 73 s p90 on a normal day and 50 / 103 s under a storm (audit 2026-08-26 §2); the old
#: 240 s cap, and before it 3 x (a 300 s read timeout + the model's 900 s retry deadline),
#: lost two storm-day rounds 1 162 s and 927 s before a second sample answered in 128 s.
SAMPLE_BUDGET_S = 900.0
#: the floor of one attempt's ``max_wait_s``: a last attempt still gets a real try
SAMPLE_MIN_WAIT_S = 20.0


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
        sample_budget_s: float | None = None,
    ):
        self.rubric: Rubric = rubric if isinstance(rubric, Rubric) else load_rubric(rubric)
        self.model_id = model_id or get_settings().default_judge
        self.n_samples = max(1, int(n_samples))
        if self.n_samples % 2 == 0:
            log.warning("judge n_samples=%d is even: exact vote ties on defects / acceptance items are decided by the "
                        "representative sample (scoring.py); an odd n gives a true majority", self.n_samples)
        self.temperature = temperature
        self.thinking = thinking
        self.max_attempts = max(1, int(max_attempts))
        self.sample_budget_s = SAMPLE_BUDGET_S if sample_budget_s is None else float(sample_budget_s)
        # payload size: the profile's dial (Settings.judge) unless the caller states one
        jd = get_settings().judge
        self.max_montages = max(1, int(jd.montages if max_montages is None else max_montages))
        self.detail_crops = max(0, int(jd.detail_crops if detail_crops is None else detail_crops))
        self.max_px = int(jd.max_px if max_px is None else max_px)
        self._model = chat_model
        self.cache_dir = cache_dir
        self.label = label

    # ------------------------------------------------------------------ provenance
    @property
    def prompt_hash(self) -> str:
        """``prompt_builder.judge_prompt_hash`` for this judge's rubric (recorded per verdict)."""
        return judge_prompt_hash(self.rubric)

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
                self.rubric, " || ".join(errors)[:2000], usage=usage, judge_backend=self.model_id, n_requested=self.n_samples,
                judge_prompt_hash=self.prompt_hash,
            )
        return aggregate_samples(
            self.rubric, samples, gates=inp.gates, acceptance_items=inp.acceptance,
            console_errors=inp.renders.console_errors, views=list(inp.renders.views), usage=usage,
            judge_backend=self.model_id, n_requested=self.n_samples, sample_errors=errors,
            judge_prompt_hash=self.prompt_hash,
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
        t_start = time.monotonic()
        for attempt in range(1, self.max_attempts + 1):
            remaining = self.sample_budget_s - (time.monotonic() - t_start)
            if remaining <= 0:
                last = (f"sample budget of {self.sample_budget_s:.0f}s spent after {attempt - 1} attempt(s)"
                        + (f"; last: {last}" if last else ""))
                log.warning("judge %s: %s", req.label, last[:300])
                break
            # the model may retry inside this call, never past what the sample can still afford
            req = req.model_copy(update={"max_wait_s": max(SAMPLE_MIN_WAIT_S, remaining)})
            t0 = time.monotonic()
            try:
                resp: ChatResponse = self.model.generate(req)
            except ModelError as e:
                usage = usage + (getattr(e, "usage", None) or Usage())  # the provider billed it
                last = f"ModelError(attempt {attempt}): {e}"
                log.warning("judge %s: %s", req.label, last)
                if not e.retryable and attempt >= 2:
                    break
                continue
            usage = usage + resp.usage
            payload = resp.parsed if resp.parsed is not None else resp.text
            try:
                out = parse_judge_output(payload, self.rubric, acceptance_ids, measured_scores=measured)
                log.debug("judge %s ok in %.1fs", req.label, time.monotonic() - t0)
                return out, usage, ""
            except JudgeParseError as e:
                last = f"parse(attempt {attempt}): {e}"
                log.warning("judge %s: %s", req.label, last[:300])
                req = req.model_copy(update={"temperature": min(1.0, self.temperature + 0.1 * attempt)})
        return None, usage, last or "unknown judge failure"


# ===================================================================== reference

SilhouetteFn = Callable[[str, str], dict[str, Any]]

#: IoU → score mapping: ≤ 0.25 → 0, ≥ 0.85 → 1, linear in between (matches the rubric anchors).
IOU_LOW, IOU_HIGH = 0.25, 0.85

#: score the measured criterion gets when the measurement cannot be trusted
#: (unreliable mask, or a reference whose proportions contradict the brief)
NEUTRAL_SCORE = 0.5

#: views handed to the mismatch pass (front-ish first, then a 3/4 and a side)
DIFF_VIEW_NAMES: tuple[str, ...] = ("front", "front_right_34", "front_left_34", "right", "left")
_ENV_DIFF = "CV3D_REFERENCE_DIFF"


def iou_to_score(iou: float) -> float:
    return max(0.0, min(1.0, (float(iou) - IOU_LOW) / (IOU_HIGH - IOU_LOW)))


def _default_silhouette_fn() -> SilhouetteFn | None:
    try:
        from codeverse.spatial.silhouette import compare_silhouette  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return None
    return compare_silhouette


def _diff_enabled(flag: bool) -> bool:
    return flag and os.environ.get(_ENV_DIFF, "on").strip().lower() not in ("0", "off", "false", "no")


def _conflict_note(info: dict[str, Any]) -> str:
    try:
        from codeverse.reference import conflict_note

        return conflict_note(info)
    except ImportError:  # pragma: no cover
        return ""


def _is_synth(note: str) -> bool:
    return note.startswith("SYNTHESIZED")


class ReferenceJudge(VlmJudge):
    """VlmJudge + reference images + measured silhouette + named mismatches."""

    name = "reference"

    def __init__(
        self,
        model_id: str | None = None,
        n_samples: int = 1,
        temperature: float = 0.2,
        *,
        rubric: str | Rubric = "reference_v1",
        silhouette_fn: SilhouetteFn | None = None,
        front_view_names: tuple[str, ...] = ("front", "front_right_34"),
        best_view: bool = True,
        diff: bool = True,
        diff_model: Any | None = None,
        diff_model_id: str = "",
        **kwargs: Any,
    ):
        super().__init__(rubric, model_id, n_samples, temperature, **kwargs)
        self.silhouette_fn = silhouette_fn or _default_silhouette_fn()
        self.front_view_names = front_view_names
        self.best_view = best_view
        self.diff = diff
        self._diff_model = diff_model
        self.diff_model_id = diff_model_id
        #: the last diff computed (tests / callers that want the mismatches themselves)
        self.last_diff: Any | None = None

    # ------------------------------------------------------------------ API
    def judge(self, inp: JudgeInput, **kwargs: Any) -> Any:
        """``VlmJudge.judge`` plus the mismatch pass's own spend.

        The diff is a real model call made from :meth:`context`; folding its
        ``Usage`` into the verdict keeps ``BudgetGuard`` and ``record.total_usage``
        honest (the ledger already sees it through the metered chat model)."""
        verdict = super().judge(inp, **kwargs)
        diff = self.last_diff
        if diff is not None and getattr(diff, "usage", None) is not None:
            verdict.usage = verdict.usage + diff.usage
        return verdict

    # ------------------------------------------------------------------ hook
    def context(self, inp: JudgeInput) -> JudgeContext:
        refs = [r for r in inp.spec.references if Path(r.path).is_file()]
        synth = bool(refs) and all(_is_synth(r.note) for r in refs)
        images: list[tuple[str, str]] = []
        for i, r in enumerate(refs[:3], 1):
            tag = "SYNTHESIZED " if _is_synth(r.note) else ""
            note = f" — {r.note}" if r.note else ""
            images.append((f"{tag}REFERENCE {i}/{min(len(refs), 3)} ({r.role}){note}", r.path))
        blocks: list[str] = []
        if synth:
            blocks.append(
                "NOTE ON THE REFERENCE: it was SYNTHESIZED from the brief by an image model, not photographed. "
                "It is a shape/part-inventory target only. Where it disagrees with the brief or the stated "
                "dimensions, the BRIEF is correct and the render must follow the brief.")
        conflict = self.dimension_conflict(inp, refs)
        note = _conflict_note(conflict)
        if note:
            blocks.append(note)
        measured_ids = [c.id for c in self.rubric.measured_criteria()]
        info: dict[str, Any] = {}
        measured_scores: dict[str, float] = {}
        if measured_ids:
            info = self.measure_silhouette(inp)
            if "iou" not in info:
                raise ReferenceJudgeError(
                    f"cannot score measured criteria {measured_ids}: {info.get('error', 'no iou')}")
            score, why = self.silhouette_score(info, conflict)
            measured_scores = {cid: score for cid in measured_ids}
            blocks.append(
                f"MEASURED SILHOUETTE (harness): {info['render']} render vs reference {info['reference']}: "
                f"IoU {info['iou']:.3f} → silhouette_match score {score:.2f}{why}."
                + (f" extra: {info['extra']}" if info.get("extra") else ""))
        elif refs:
            info = self.measure_silhouette(inp)
        diff = self.reference_diff(inp, refs, info, synthesized=synth) if refs else None
        self.last_diff = diff
        if diff is not None and diff.as_text():
            blocks.append(diff.as_text())
        return JudgeContext(measured_scores=measured_scores, extra_text="\n\n".join(blocks), extra_images=images)

    # ------------------------------------------------------------------ proportion guard
    def dimension_conflict(self, inp: JudgeInput, refs: list[Any]) -> dict[str, Any]:
        """Does the target reference's own outline contradict the brief's dimensions?

        When it does, no numeric proportion signal derived from that picture may be
        used against the object — the brief wins (see
        :mod:`codeverse.reference.proportions`).
        """
        targets = [r for r in refs if r.role == "target"] or refs
        if not targets:
            return {"conflict": False}
        try:
            from codeverse.reference import dimension_conflict

            return dimension_conflict(inp.spec, targets[0].path)
        except Exception as e:  # noqa: BLE001 — advisory
            log.warning("dimension-conflict check failed: %s", e)
            return {"conflict": False, "error": str(e)}

    def silhouette_score(self, info: dict[str, Any], conflict: dict[str, Any]) -> tuple[float, str]:
        """IoU → measured score, NEUTRAL when the reference contradicts the brief or the
        mask is unreliable.  Returns ``(score, ' (why)')``."""
        if conflict.get("conflict"):
            return NEUTRAL_SCORE, " (NEUTRAL: the reference's proportions contradict the brief's dimensions)"
        if info.get("reliable") is False:
            return NEUTRAL_SCORE, " (NEUTRAL: the background mask was unreliable)"
        return iou_to_score(info["iou"]), ""

    # ------------------------------------------------------------------ mismatch pass
    def reference_diff(self, inp: JudgeInput, refs: list[Any], info: dict[str, Any], *, synthesized: bool) -> Any | None:
        """One extra vision call naming concrete mismatches.  ``None`` when disabled;
        never raises (a failed diff simply contributes no text)."""
        if not _diff_enabled(self.diff):
            return None
        try:
            from codeverse.reference import compare
        except ImportError:  # pragma: no cover - the package is part of the wheel
            return None
        targets = [r.path for r in refs if r.role == "target"] or [r.path for r in refs]
        renders = self.diff_views(list(inp.renders.views))
        measured = dict(info) if "iou" in info else None
        if measured is not None:
            measured.setdefault("view", info.get("render", ""))
        try:
            return compare(inp.spec, targets, renders, model=self._pick_diff_model(),
                           part_names=_plan_part_names(inp), measured=measured, synthesized=synthesized)
        except Exception as e:  # noqa: BLE001 — an advisory pass must never fail a verdict
            log.warning("reference diff pass failed: %s", e)
            return None

    def _pick_diff_model(self) -> Any:
        """``diff_model`` > ``diff_model_id`` (a cheaper perception model) > the judge's own model."""
        if self._diff_model is not None:
            return self._diff_model
        if self.diff_model_id:
            from codeverse.models import get_chat_model

            self._diff_model = get_chat_model(self.diff_model_id)
            return self._diff_model
        return self.model

    def diff_views(self, views: list[RenderView]) -> list[str]:
        """Up to 3 shaded views for the diff, front-ish first."""
        by_name = {v.name: v for v in views}
        picked = [by_name[n].path for n in DIFF_VIEW_NAMES if n in by_name]
        for v in views:
            if len(picked) >= 3:
                break
            if v.path not in picked:
                picked.append(v.path)
        return picked[:3]

    # ------------------------------------------------------------------ silhouette
    def pick_front_view(self, views: list[RenderView]) -> RenderView | None:
        for name in self.front_view_names:
            for v in views:
                if v.name == name:
                    return v
        return views[0] if views else None

    def measure_silhouette(self, inp: JudgeInput) -> dict[str, Any]:
        """{iou, render, reference, extra} or {error}.

        With ``best_view=True`` (default) the IoU is taken from the render view that
        best matches the reference's camera instead of a hardcoded ``front``: a
        reference photograph has ONE camera, and comparing a three-quarter product
        shot to a straight-on elevation measures camera agreement, not shape.  The
        winning view name travels in ``render`` so the number stays auditable.
        """
        targets = [r for r in inp.spec.references if r.role == "target" and Path(r.path).is_file()] or [
            r for r in inp.spec.references if Path(r.path).is_file()]
        if not targets:
            return {"error": "spec has no readable reference images"}
        views = list(inp.renders.views)
        if not views:
            return {"error": "render set has no views"}
        if self.silhouette_fn is None:
            return {"error": "compare_silhouette unavailable (codeverse.spatial.silhouette not importable)"}
        res, name = self._measure(views, targets[0].path)
        if not isinstance(res, dict) or "iou" not in res:
            return {"error": f"compare_silhouette returned no iou: {res!r}"}
        extra = {k: v for k, v in res.items() if k not in ("iou", "per_view") and isinstance(v, (int, float, str, bool))}
        out = {"iou": float(res["iou"]), "render": name, "reference": Path(targets[0].path).name,
               "reliable": bool(res.get("reliable", True)),
               "aspect_ratio_err": res.get("aspect_ratio_err"), "extra": extra}
        if res.get("per_view"):
            out["per_view"] = res["per_view"]
        return out

    def _measure(self, views: list[RenderView], reference: str) -> tuple[dict[str, Any], str]:
        """(result, view name).  Best-matching view when enabled and available."""
        if self.best_view and self.silhouette_fn is _default_silhouette_fn():
            try:
                from codeverse.spatial.silhouette import best_view_match
            except ImportError:  # pragma: no cover
                best_view_match = None  # type: ignore[assignment]
            if best_view_match is not None:
                res = best_view_match(views, reference)
                if "iou" in res:
                    return res, str(res.get("view", ""))
        view = self.pick_front_view(views)
        if view is None:  # pragma: no cover - guarded by the caller
            return {}, ""
        return self.silhouette_fn(view.path, reference), view.name  # type: ignore[misc]


def _plan_part_names(inp: JudgeInput) -> list[str]:
    """Part names from the plan digest the judge already receives (best effort).

    ``judges.replay_input.plan_digest`` writes them as one ``Parts: A, B×2, C`` segment;
    a multi-line digest lists one ``- Name · role · …`` per line.  Both are handled, and
    an unrecognised digest simply yields no names (the diff prompt then says so).
    """
    text = getattr(inp, "plan_summary", "") or ""
    names: list[str] = []
    for chunk in re.split(r"(?i)\bparts\s*:", text)[1:]:
        for raw in chunk.split("\n")[0].split(","):
            names.append(raw.split("\u00d7")[0].split("(")[0].strip().rstrip("."))
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("-", "*", "\u00b7")):
            continue
        head = line.lstrip("-*\u00b7 ").split("\u00b7")[0].split("(")[0].split(":")[0].strip()
        names.append(head)
    seen: list[str] = []
    for n in names:
        if n and " " not in n and 2 < len(n) <= 40 and n[:1].isupper() and n not in seen:
            seen.append(n)
    return seen[:40]


LIKENESS_NOTE = (
    "REAL-WORLD REFERENCE PHOTOS are attached (labelled REAL-WORLD REFERENCE n/N).  They are not a "
    "composition to copy; they are what the thing the brief names ACTUALLY looks like.  Score "
    "brief fidelity, colour/light and richness against them: dominant colour and where the "
    "secondary colours sit, how the structure folds / layers / thins out, where the brightness "
    "concentrates and how much of the frame stays dark, what the real texture is at fine scale.  A "
    "frame that ticks every noun in the brief but would never be mistaken for the photographed "
    "thing (a row of evenly spaced bars standing in for curtains, a flat band standing in for a "
    "gradient of light, cartoon-saturated colour where the photo is subtle) is a 0.4-0.5 on brief "
    "fidelity, not a 1.0.  Foreground, framing and landscape in the photos are incidental unless "
    "the brief asks for them."
)


class LikenessJudge(VlmJudge):
    """``VlmJudge`` + the reference photos of the REAL thing — for tracks with no silhouette.

    ``ReferenceJudge`` is built for objects: it measures a front-view silhouette IoU against
    the target photo and runs a part-inventory mismatch pass.  Neither means anything for a
    fragment shader or a scene, where a reference photo answers a different question — does
    this LOOK like the thing?  Measured 2026-08-26 (teaser aurora, three versions, flash and
    codex): every version scored 0.78-0.94 with an empty issues list while none resembled an
    aurora (a comb of straight teal bars; pink cotton-wool lobes), because the brief is a
    checklist of nouns and the rubric had nothing to say about likeness.  This judge attaches
    up to three photos beside the frames with :data:`LIKENESS_NOTE` and leaves the rubric alone.
    """

    name = "likeness"

    def __init__(self, model_id: str | None = None, n_samples: int = 1, temperature: float = 0.2, *,
                 rubric: str | Rubric = "shader_v2", max_refs: int = 3, **kwargs: Any):
        super().__init__(rubric, model_id, n_samples, temperature, **kwargs)
        self.max_refs = max(1, int(max_refs))

    def context(self, inp: JudgeInput) -> JudgeContext:
        if self.rubric.measured_criteria():
            raise ValueError(f"rubric {self.rubric.name} has measured criteria; LikenessJudge measures nothing")
        refs = [r for r in inp.spec.references if Path(r.path).is_file()][: self.max_refs]
        images = [(f"REAL-WORLD REFERENCE {i}/{len(refs)}" + (f" — {r.note}" if r.note else ""), r.path)
                  for i, r in enumerate(refs, 1)]
        return JudgeContext(extra_text=LIKENESS_NOTE if refs else "", extra_images=images)


class ReferenceJudgeError(RuntimeError):
    """The reference judge cannot compute its measured criteria."""
