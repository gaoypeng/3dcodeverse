"""One call the CLI makes: ground a run in a synthesized reference image.

``ground_spec(spec, ws, …)`` synthesizes + validates the reference, attaches it
to the spec under the honesty guards of :mod:`.attach`, rewrites ``spec.json``,
records the whole ``ReferenceSet`` (verdicts included) under
``artifacts/reference/`` and emits ``reference.*`` events.

Everything downstream then works with no further wiring, because a spec that
carries references already means something to the harness: the planner receives
the images inline, the generator prompt gets the reference note and the
``compare_reference`` / ``compare_silhouette`` tools, static runs gain the
``reference_silhouette`` gate and its refine task, and the verdict is produced
by :class:`codeverse.judges.reference.ReferenceJudge` on ``reference_v1``.

Never raises: a run that cannot get a reference runs exactly as it would have
without one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from codeverse.contracts.spec import Spec
from codeverse.reference.attach import attach, has_user_references
from codeverse.reference.synth import synth_reference
from codeverse.reference.types import ReferenceSet

log = logging.getLogger(__name__)

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
    from codeverse.models import get_chat_model

    return get_chat_model(model_id)


def _image_model(model_id: str = "") -> Any:
    from codeverse.models.gemini_image import DEFAULT_IMAGE_MODEL, GeminiImageModel

    return GeminiImageModel(model_id or DEFAULT_IMAGE_MODEL)


def _emit(events: Any, name: str, **payload: Any) -> None:
    if events is None:
        return
    try:
        events.emit(name, **payload)
    except Exception:  # noqa: BLE001 - telemetry must never break a run
        log.debug("could not emit %s", name, exc_info=True)


__all__ = ["ARTIFACT_SUBDIR", "ground_spec"]
