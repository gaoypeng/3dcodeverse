"""``VlmJudge``: rubric YAML + labelled renders + measurements → ``Judgment``.

Per sample, one ``ChatRequest`` with a per-rubric JSON schema; samples differ by montage order so the
n-sample std measures judge noise.  Score, penalties, floors, caps and pass are computed in code
(``rubrics.aggregate_samples``).  Retries stay inside one ``sample_budget_s``; if no sample succeeds a
*degraded* Judgment (``raw.status='degraded'``) is returned, which the orchestrator treats as a glitch.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import Judgment, RenderView
from codeverse3d.contracts.chat import ChatRequest, ChatResponse
from codeverse3d.contracts.common import TRACK_INFO, Track, Usage
from codeverse3d.conventions import views_by_preference
from codeverse3d.judges.base import SLICE_TRACKS, JudgeInput
from codeverse3d.judges.prompt_builder import (
    _key,
    _stat,
    build_judge_messages,
    connectivity_error_pairs,
    default_cache_dir,
    judge_prompt_hash,
)
from codeverse3d.judges.rubrics import (
    JudgeOutput,
    JudgeParseError,
    Rubric,
    aggregate_samples,
    degraded_judgment,
    load_rubric,
    parse_judge_output,
    wire_schema,
)
from codeverse3d.models import get_chat_model
from codeverse3d.models.base import ChatModel, ModelError
from codeverse3d.proc import fan_out, unique_tmp
from codeverse3d.reference import compare, conflict_note, dimension_conflict
from codeverse3d.spatial.silhouette import (
    CompareFn,
    ReferenceMatch,
    compare_silhouette,
    measure_reference,
)

log = logging.getLogger(__name__)

#: retry budget for ONE judge sample, every attempt included
SAMPLE_BUDGET_S = 900.0
SAMPLE_MIN_WAIT_S = 20.0

SLICE_LABELS = {
    "front_back": "cross-section front-back · interior view",
    "left_right": "cross-section left-right · interior view",
}

THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high")

#: per-track montage ceilings under the ``Settings.judge.montages`` dial
TRACK_MONTAGE_CAP: dict[str, int] = {"scene": 4, "graphics": 3}


@dataclass
class JudgeContext:

    measured_scores: dict[str, float] = field(default_factory=dict)
    extra_text: str = ""
    extra_images: list[tuple[str, str]] = field(default_factory=list)
    #: what building the context cost; folded into the verdict so budgets see it
    usage: Usage = field(default_factory=Usage)


class VlmJudge:

    name = "vlm"

    def __init__(
        self,
        rubric: str | Rubric = "static_object",
        model_id: str | None = None,
        n_samples: int = 1,
        temperature: float = 0.2,
        *,
        thinking: Literal["off", "low", "medium", "high"] | None = None,
        max_attempts: int = 3,
        chat_model: ChatModel | None = None,
        cache_dir: Path | None = None,
        label: str = "judge",
        sample_budget_s: float | None = None,
        fixed_order: bool = False,
    ):
        self.rubric: Rubric = rubric if isinstance(rubric, Rubric) else load_rubric(rubric)
        # fixed_order sends the identical prompt n times (re-judge sigma) instead of shuffling view order
        self.fixed_order = bool(fixed_order)
        self.model_id = model_id or get_settings().default_judge
        self.n_samples = max(1, int(n_samples))
        if self.n_samples % 2 == 0:
            log.warning("judge n_samples=%d is even: an exact vote tie on a defect reads as absent and a tie on an "
                        "acceptance item follows the representative sample (rubrics.aggregate_samples); an odd n "
                        "gives a true majority", self.n_samples)
        self.temperature = temperature
        jd = get_settings().judge
        # ``thinking=None`` reads ``Settings.judge.thinking``
        level = str(jd.thinking if thinking is None else thinking)
        if level not in THINKING_LEVELS:
            raise ValueError(f"judge thinking must be one of {' | '.join(THINKING_LEVELS)}, not {level!r}")
        self.thinking: str = level
        self.max_attempts = max(1, int(max_attempts))
        self.sample_budget_s = SAMPLE_BUDGET_S if sample_budget_s is None else float(sample_budget_s)
        self.max_montages = max(1, int(jd.montages))
        self.detail_crops = max(0, int(jd.detail_crops))
        self.max_px = int(jd.max_px)
        self._model = chat_model
        self.cache_dir = cache_dir
        self.label = label

    def _montages_for(self, inp: JudgeInput) -> int:
        """Per-track montage cap (``TRACK_MONTAGE_CAP``); the object rig takes the dial."""
        cap = TRACK_MONTAGE_CAP.get(inp.spec.track.value)
        if cap is None:
            return self.max_montages
        return min(self.max_montages, cap)

    @property
    def prompt_hash(self) -> str:
        return judge_prompt_hash(self.rubric)

    @property
    def model(self) -> ChatModel:
        if self._model is None:
            self._model = get_chat_model(self.model_id)
        return self._model

    def judge(self, inp: JudgeInput) -> Judgment:
        """Judge one round; clay/normals views in ``inp.renders`` are routed to the geometry montage."""
        geometry_views = inp.geometry_views
        acceptance_ids = [a.id for a in inp.acceptance]
        schema = wire_schema(self.rubric, acceptance_ids)
        ctx = self.context(inp)
        measured, extra_text, extra_images = ctx.measured_scores, ctx.extra_text, ctx.extra_images
        usage = ctx.usage
        slice_images, elicit = self.slice_payload(inp)
        samples: list[JudgeOutput] = []
        errors: list[str] = []
        reqs: list[ChatRequest] = []
        for k in range(self.n_samples):
            seed = None if (self.fixed_order or (self.n_samples == 1 and k == 0)) else (inp.round_index * 1000 + k)
            system, messages = build_judge_messages(
                inp, self.rubric, shuffle_seed=seed, geometry_views=geometry_views, max_montages=self._montages_for(inp),
                detail_crops=self.detail_crops, max_px=self.max_px, cache_dir=self.cache_dir,
                extra_images=extra_images, extra_text=extra_text,
                slice_images=slice_images, provenance_elicitation=elicit,
            )
            reqs.append(ChatRequest(
                messages=messages, system=system, response_schema=schema, temperature=self.temperature,
                thinking=self.thinking,  # type: ignore[arg-type]
                # fan_out copies contextvars into every sample thread; the label adds what only this call knows
                label=f"{self.label}:{self.rubric.name}:r{inp.round_index:02d}:s{k}",
            ))
        if len(reqs) == 1:
            outcomes = [self._sample(reqs[0], acceptance_ids, measured)]
        else:
            self.model  # noqa: B018
            results = fan_out(
                reqs, lambda r: self._sample(r, acceptance_ids, measured),
                max_workers=len(reqs), label=f"{self.label}:samples", item_name=lambda r: r.label,
            )
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

    def context(self, inp: JudgeInput) -> JudgeContext:
        """Per-call extras: measured-criterion scores, extra text and extra images (none in the base judge)."""
        if self.rubric.measured_criteria():
            raise ValueError(
                f"rubric {self.rubric.name} has measured criteria "
                f"{[c.id for c in self.rubric.measured_criteria()]} but VlmJudge computes none; use a specialised judge"
            )
        return JudgeContext()

    def slice_payload(self, inp: JudgeInput) -> tuple[list[tuple[str, str]], bool]:
        """``([(label, png_path)…], provenance_elicitation)`` for the slice channel.

        Fires only when ``Settings.judge.slices == "on-error"``, the track is an object track, ``inp.glb_path``
        exists and the connectivity gate has an ERROR; otherwise ``([], False)``.  Never raises.
        """
        if get_settings().judge.slices != "on-error":
            return [], False
        if inp.spec.track.value not in SLICE_TRACKS or not inp.glb_path:
            return [], False
        glb = Path(inp.glb_path)
        if not glb.is_file():
            return [], False
        if not any(g.gate == "connectivity" and g.errors for g in inp.gates):
            return [], False
        pairs = connectivity_error_pairs(inp.gates)
        try:
            out_dir, manifest = self._render_slices(glb, pairs)
        except ImportError as e:
            log.warning("judge slices disabled: %s (install the 'mesh' extra)", e)
            return [], False
        except Exception as e:  # noqa: BLE001
            log.warning("judge slice render failed for %s: %s", glb, e)
            return [], True
        return [(SLICE_LABELS[s.name], str(out_dir / s.png)) for s in manifest.rendered()], True

    def _render_slices(self, glb: Path, pairs: list[tuple[str, str]]):
        """Render (or reuse) the slice set for ``glb``, cached by path + mtime + size + error pairs."""
        from codeverse3d.spatial.sections import SliceManifest, judge_slices

        cache = Path(self.cache_dir) if self.cache_dir else default_cache_dir()
        out_dir = cache / f"slices_{_key([_stat(str(glb)), str(pairs)])}"
        mpath = out_dir / "manifest.json"
        if not mpath.is_file():
            tmp = unique_tmp(out_dir)
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                judge_slices(glb, pairs, tmp)
                with contextlib.suppress(OSError):
                    tmp.rename(out_dir)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return out_dir, SliceManifest.model_validate_json(mpath.read_text())

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
            req = req.model_copy(update={"max_wait_s": max(SAMPLE_MIN_WAIT_S, remaining)})
            t0 = time.monotonic()
            try:
                resp: ChatResponse = self.model.generate(req)
            except ModelError as e:
                usage = usage + (getattr(e, "usage", None) or Usage())
                last = f"ModelError(attempt {attempt}): {e}"
                log.warning("judge %s: %s", req.label, last)
                if not e.retryable:
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



#: IoU → score: ≤ 0.25 → 0, ≥ 0.85 → 1, linear between
IOU_LOW, IOU_HIGH = 0.25, 0.85

#: score the measured criterion gets when the measurement cannot be trusted
NEUTRAL_SCORE = 0.5

#: views handed to the mismatch pass (front-ish first, then a 3/4 and a side)
DIFF_VIEW_NAMES: tuple[str, ...] = ("front", "front_right_high", "front_left_high", "right", "left")


def iou_to_score(iou: float) -> float:
    return max(0.0, min(1.0, (float(iou) - IOU_LOW) / (IOU_HIGH - IOU_LOW)))


def _diff_enabled(flag: bool) -> bool:
    return flag and get_settings().reference_diff


class ReferenceJudge(VlmJudge):

    name = "reference"

    def __init__(
        self,
        model_id: str | None = None,
        n_samples: int = 1,
        temperature: float = 0.2,
        *,
        rubric: str | Rubric = "reference",
        silhouette_fn: CompareFn | None = None,
        diff: bool = True,
        **kwargs: Any,
    ):
        super().__init__(rubric, model_id, n_samples, temperature, **kwargs)
        self.silhouette_fn = silhouette_fn or compare_silhouette
        self.diff = diff

    def context(self, inp: JudgeInput) -> JudgeContext:
        refs = [r for r in inp.spec.references if Path(r.path).is_file()]
        images: list[tuple[str, str]] = []
        for i, r in enumerate(refs[:3], 1):
            note = f" — {r.note}" if r.note else ""
            images.append((f"REFERENCE {i}/{min(len(refs), 3)} ({r.role}){note}", r.path))
        blocks: list[str] = []
        conflict = self.dimension_conflict(inp, refs)
        note = conflict_note(conflict)
        if note:
            blocks.append(note)
        measured_ids = [c.id for c in self.rubric.measured_criteria()]
        measured_scores: dict[str, float] = {}
        match = measure_reference(inp.renders.views, inp.spec, compare=self.silhouette_fn) if refs else None
        if measured_ids:
            if match is None:
                raise ReferenceJudgeError(f"cannot score measured criteria {measured_ids}: "
                                          + ("no render view compared" if refs else "spec has no readable reference images"))
            score, why = self.silhouette_score(match)
            measured_scores = {cid: score for cid in measured_ids}
            extra = match.model_dump(exclude={"iou", "reference", "conflict", "per_view"}, exclude_none=True)
            blocks.append(
                f"MEASURED SILHOUETTE (harness): {match.view} render vs reference {Path(match.reference).name}: "
                f"IoU {match.iou:.3f} → silhouette_match score {score:.2f}{why}. extra: {extra}")
        diff = self.reference_diff(inp, refs, match) if refs else None
        if diff is not None and diff.as_text():
            blocks.append(diff.as_text())
        return JudgeContext(measured_scores=measured_scores, extra_text="\n\n".join(blocks), extra_images=images,
                            usage=diff.usage if diff is not None else Usage())

    def dimension_conflict(self, inp: JudgeInput, refs: list[Any]) -> dict[str, Any]:
        """Does the target reference's own outline contradict the brief's dimensions?  If so the brief wins."""
        targets = [r for r in refs if r.role == "target"] or refs
        if not targets:
            return {"conflict": False}
        try:
            return dimension_conflict(inp.spec, targets[0].path)
        except Exception as e:  # noqa: BLE001
            log.warning("dimension-conflict check failed: %s", e)
            return {"conflict": False, "error": str(e)}

    def silhouette_score(self, match: ReferenceMatch) -> tuple[float, str]:
        """IoU → measured score, neutral when the reference contradicts the brief or the mask is unreliable."""
        if match.conflict:
            return NEUTRAL_SCORE, " (NEUTRAL: the reference's proportions contradict the brief's dimensions)"
        if not match.reliable:
            return NEUTRAL_SCORE, " (NEUTRAL: the background mask was unreliable)"
        return iou_to_score(match.iou), ""

    def reference_diff(self, inp: JudgeInput, refs: list[Any], match: ReferenceMatch | None) -> Any | None:
        """One extra vision call naming concrete mismatches; ``None`` when disabled; never raises."""
        if not _diff_enabled(self.diff):
            return None
        targets = [r.path for r in refs if r.role == "target"] or [r.path for r in refs]
        renders = self.diff_views(list(inp.renders.views))
        measured = None if match is None else {"iou": match.iou, "aspect_ratio_err": match.aspect_ratio_err, "reliable": match.reliable,
                                                "view": match.view, "reference": Path(match.reference).name}
        try:
            return compare(inp.spec, targets, renders, model=self.model,
                           part_names=inp.part_names, measured=measured, thinking=self.thinking)  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001
            log.warning("reference diff pass failed: %s", e)
            return None

    def diff_views(self, views: list[RenderView]) -> list[str]:
        picked = [v.path for v in views_by_preference(views, DIFF_VIEW_NAMES)]
        for v in views:
            if len(picked) >= 3:
                break
            if v.path not in picked:
                picked.append(v.path)
        return picked[:3]


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
    """``VlmJudge`` + reference photos, for tracks with no silhouette: the photos are attached beside the
    frames with :data:`LIKENESS_NOTE` and the rubric is unchanged."""

    name = "likeness"

    def __init__(self, model_id: str | None = None, n_samples: int = 1, temperature: float = 0.2, *,
                 rubric: str | Rubric = "shader", **kwargs: Any):
        super().__init__(rubric, model_id, n_samples, temperature, **kwargs)

    def context(self, inp: JudgeInput) -> JudgeContext:
        if self.rubric.measured_criteria():
            raise ValueError(f"rubric {self.rubric.name} has measured criteria; LikenessJudge measures nothing")
        refs = [r for r in inp.spec.references if Path(r.path).is_file()][:3]
        images = [(f"REAL-WORLD REFERENCE {i}/{len(refs)}" + (f" — {r.note}" if r.note else ""), r.path)
                  for i, r in enumerate(refs, 1)]
        return JudgeContext(extra_text=LIKENESS_NOTE if refs else "", extra_images=images)


class ReferenceJudgeError(RuntimeError):
    """The reference judge cannot compute its measured criteria."""


def judge_for(track: Track, rubric: str | Rubric, *, references: bool) -> type[VlmJudge]:
    """The judge class that scores a round, shared by the live run and every replay: :class:`ReferenceJudge`
    for measured criteria or object-track references, :class:`LikenessJudge` for graphics/scene references,
    else :class:`VlmJudge`."""
    rub = rubric if isinstance(rubric, Rubric) else load_rubric(rubric)
    if rub.measured_criteria() or (references and not TRACK_INFO[track].likeness_refs):
        return ReferenceJudge
    return LikenessJudge if references else VlmJudge
