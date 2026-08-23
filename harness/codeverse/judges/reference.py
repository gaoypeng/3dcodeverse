"""``ReferenceJudge``: image-conditioned judging (rubric ``reference_v1``).

Adds the spec's reference images BEFORE the renders (labelled ``REFERENCE k``)
and scores ``silhouette_match`` IN CODE from the front-render-vs-reference
outline IoU (``codeverse.spatial.silhouette.compare_silhouette`` when present)
— the VLM only scores the perceptual criteria.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import RenderView
from codeverse.judges.base import JudgeInput
from codeverse.judges.rubrics import Rubric
from codeverse.judges.vlm_judge import JudgeContext, VlmJudge

log = logging.getLogger(__name__)

SilhouetteFn = Callable[[str, str], dict[str, Any]]

#: IoU → score mapping: ≤ 0.25 → 0, ≥ 0.85 → 1, linear in between (matches the rubric anchors).
IOU_LOW, IOU_HIGH = 0.25, 0.85


def iou_to_score(iou: float) -> float:
    return max(0.0, min(1.0, (float(iou) - IOU_LOW) / (IOU_HIGH - IOU_LOW)))


def _default_silhouette_fn() -> SilhouetteFn | None:
    try:
        from codeverse.spatial.silhouette import compare_silhouette  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return None
    return compare_silhouette


class ReferenceJudge(VlmJudge):
    """VlmJudge + reference images + measured silhouette criterion."""

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
        **kwargs: Any,
    ):
        super().__init__(rubric, model_id, n_samples, temperature, **kwargs)
        self.silhouette_fn = silhouette_fn or _default_silhouette_fn()
        self.front_view_names = front_view_names

    # ------------------------------------------------------------------ hook
    def context(self, inp: JudgeInput) -> JudgeContext:
        refs = [r for r in inp.spec.references if Path(r.path).is_file()]
        images: list[tuple[str, str]] = []
        for i, r in enumerate(refs[:3], 1):
            note = f" — {r.note}" if r.note else ""
            images.append((f"REFERENCE {i}/{min(len(refs), 3)} ({r.role}){note}", r.path))
        measured_ids = [c.id for c in self.rubric.measured_criteria()]
        if not measured_ids:
            return JudgeContext(extra_images=images)
        info = self.measure_silhouette(inp)
        if "iou" not in info:
            raise ReferenceJudgeError(f"cannot score measured criteria {measured_ids}: {info.get('error', 'no iou')}")
        score = iou_to_score(info["iou"])
        text = (
            f"MEASURED SILHOUETTE (harness): front render {info['render']} vs reference {info['reference']}: "
            f"IoU {info['iou']:.3f} → silhouette_match score {score:.2f}."
            + (f" extra: {info['extra']}" if info.get("extra") else "")
        )
        return JudgeContext(measured_scores={cid: score for cid in measured_ids}, extra_text=text, extra_images=images)

    # ------------------------------------------------------------------ silhouette
    def pick_front_view(self, views: list[RenderView]) -> RenderView | None:
        for name in self.front_view_names:
            for v in views:
                if v.name == name:
                    return v
        return views[0] if views else None

    def measure_silhouette(self, inp: JudgeInput) -> dict[str, Any]:
        """{iou, render, reference, extra} or {error}."""
        targets = [r for r in inp.spec.references if r.role == "target" and Path(r.path).is_file()] or [
            r for r in inp.spec.references if Path(r.path).is_file()
        ]
        if not targets:
            return {"error": "spec has no readable reference images"}
        view = self.pick_front_view(inp.renders.views)
        if view is None:
            return {"error": "render set has no views"}
        if self.silhouette_fn is None:
            return {"error": "compare_silhouette unavailable (codeverse.spatial.silhouette not importable)"}
        res = self.silhouette_fn(view.path, targets[0].path)
        if not isinstance(res, dict) or "iou" not in res:
            return {"error": f"compare_silhouette returned no iou: {res!r}"}
        extra = {k: v for k, v in res.items() if k != "iou" and isinstance(v, (int, float, str, bool))}
        return {"iou": float(res["iou"]), "render": view.name, "reference": Path(targets[0].path).name, "extra": extra}


class ReferenceJudgeError(RuntimeError):
    """The reference judge cannot compute its measured criteria."""
