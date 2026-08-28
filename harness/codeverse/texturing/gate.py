"""Do-no-harm ship gate for a texture pass.

1. ``seam_gate``: per-texture seam score (``tile.seam_score`` after mirror-blend)
   must be ≤ ``SEAM_MAX``; failing textures are dropped (their parts keep flat
   materials) — deterministic, free.
2. ``judge_gate``: render BEFORE and AFTER (``render_glb``, quick 4 views) and
   judge both with the same ``VlmJudge`` (rubric of the track, n_samples=1).
   Ship iff ``Δoverall ≥ min_overall_delta`` (default −0.01) AND the rubric's
   material criterion improved (``> min_materials_delta``, default 0).  A degraded
   verdict on either side → not shipped (a glitch is never a score).

Everything is injectable (``render`` / ``judge`` callables) so tests run offline.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import Measurement, RenderSet
from codeverse.contracts.common import Usage
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem, StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.conventions import OBJECT_VIEWS_QUICK, ViewPreset
from codeverse.texturing.generate import TextureAsset
from codeverse.texturing.tile import SEAM_MAX

log = logging.getLogger(__name__)

MIN_OVERALL_DELTA = -0.01
MIN_MATERIALS_DELTA = 0.0


class SeamGateResult(BaseModel):
    passed: dict[str, float] = Field(default_factory=dict, description="texture_id → seam score (kept)")
    failed: dict[str, float] = Field(default_factory=dict, description="texture_id → seam score (dropped)")
    threshold: float = SEAM_MAX


def seam_gate(textures: dict[str, TextureAsset], *, max_seam: float = SEAM_MAX) -> SeamGateResult:
    res = SeamGateResult(threshold=max_seam)
    for tid, a in textures.items():
        if not a.ok:
            continue
        (res.passed if a.seam_score <= max_seam else res.failed)[tid] = round(float(a.seam_score), 4)
    return res


class GateResult(BaseModel):
    shipped: bool
    reason: str = ""
    overall_before: float | None = None
    overall_after: float | None = None
    delta: float | None = None
    materials_criterion: str = ""
    materials_before: float | None = None
    materials_after: float | None = None
    materials_delta: float | None = None
    renders_before: RenderSet | None = None
    renders_after: RenderSet | None = None
    judgment_before: Judgment | None = None
    judgment_after: Judgment | None = None
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0


def material_criterion(scores: dict[str, float], rubric_hint: str = "") -> str:
    """The rubric criterion that tracks surface quality (``materials``,
    ``material_color``, ``materials_shaders_effects``, ``material_truth`` ...)."""
    for k in scores:
        if k.startswith("material"):
            return k
    for k in scores:
        if "material" in k or "texture" in k or "surface" in k:
            return k
    return ""


def _plan_summary(plan: StaticPlan | None) -> str:
    if plan is None:
        return ""
    parts = ", ".join(f"{p.name}×{p.instances}" if p.instances > 1 else p.name for p in plan.parts)
    e = plan.overall_bbox.extents
    return f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m. Parts: {parts}."


def _degraded(j: Judgment) -> bool:
    from codeverse.judges.rubrics import is_degraded

    return is_degraded(j)


def judge_gate(
    spec: Spec,
    plan: StaticPlan | None,
    glb_before: Path,
    glb_after: Path,
    out_dir: Path,
    *,
    judge: Any,
    measurement: Measurement | None = None,
    views: Sequence[ViewPreset] = OBJECT_VIEWS_QUICK,
    render: Callable[..., RenderSet] | None = None,
    size: int = 512,
    min_overall_delta: float = MIN_OVERALL_DELTA,
    min_materials_delta: float = MIN_MATERIALS_DELTA,
) -> GateResult:
    """Render + judge both GLBs; decide.  ``judge`` is any object with
    ``.judge(JudgeInput) -> Judgment`` (``VlmJudge`` or a fake)."""
    from codeverse.judges.base import JudgeInput

    t0 = time.time()
    if render is None:
        from codeverse.spatial.render import render_glb

        render = render_glb
    out_dir = Path(out_dir)
    rs_before = render(glb_before, out_dir / "before", views=list(views), width=size, height=size)
    rs_after = render(glb_after, out_dir / "after", views=list(views), width=size, height=size)
    acceptance: list[AcceptanceItem] = list(getattr(plan, "acceptance", []) or [])
    summary = _plan_summary(plan)
    res = GateResult(shipped=False, renders_before=rs_before, renders_after=rs_after)
    jb = judge.judge(JudgeInput(spec=spec, renders=rs_before, measurement=measurement, acceptance=acceptance,
                                plan_summary=summary, round_index=0,
                                extra_context="Texture gate: BEFORE texturing (flat materials)."))
    ja = judge.judge(JudgeInput(spec=spec, renders=rs_after, measurement=measurement, acceptance=acceptance,
                                plan_summary=summary, round_index=0,
                                extra_context="Texture gate: AFTER texturing (image textures applied)."))
    res.judgment_before, res.judgment_after = jb, ja
    res.usage = jb.usage + ja.usage
    res.duration_s = round(time.time() - t0, 2)
    if _degraded(jb) or _degraded(ja):
        res.reason = "judge degraded on one side — not shipped"
        return res
    res.overall_before, res.overall_after = float(jb.overall), float(ja.overall)
    res.delta = round(res.overall_after - res.overall_before, 4)
    crit = material_criterion(ja.scores)
    res.materials_criterion = crit
    if crit:
        res.materials_before = float(jb.scores.get(crit, 0.0))
        res.materials_after = float(ja.scores.get(crit, 0.0))
        res.materials_delta = round(res.materials_after - res.materials_before, 4)
    ok_overall = res.delta >= min_overall_delta
    ok_mat = (res.materials_delta is None) or (res.materials_delta > min_materials_delta)
    if crit and res.materials_delta is None:
        ok_mat = False
    res.shipped = bool(ok_overall and ok_mat)
    if res.shipped:
        res.reason = f"Δoverall {res.delta:+.3f} ≥ {min_overall_delta:+.2f} and {crit or 'overall'} {res.materials_delta:+.3f}" if crit \
            else f"Δoverall {res.delta:+.3f} ≥ {min_overall_delta:+.2f}"
    else:
        why = []
        if not ok_overall:
            why.append(f"Δoverall {res.delta:+.3f} < {min_overall_delta:+.2f}")
        if not ok_mat:
            why.append(f"{crit} {res.materials_delta:+.3f} did not improve")
        res.reason = "; ".join(why)
    return res
