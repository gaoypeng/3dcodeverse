"""Reference-image support for object tracks.

When ``spec.references`` is non-empty the planner already sees the images; the
generator prompts carry a reference note (``prompting.reference_note``) and, for
single-shot, the images themselves; the main judge becomes ``ReferenceJudge``
(rubric ``reference_v1``).  This module adds the deterministic part:

* ``silhouette_gate`` — after rendering, the front view's outline IoU against
  the *target* reference (``spatial.silhouette.compare_silhouette``) becomes a
  ``reference_silhouette`` gate finding (WARN, with the numbers in ``data``).
* ``reference_refine_tasks`` — when that IoU is below ``IOU_REFINE_THRESHOLD``
  the next refine round gets a priority-1 task saying so, with the numbers.
"""

from __future__ import annotations

import logging
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, RenderSet, RenderView, Severity
from codeverse.contracts.run import RoundRecord
from codeverse.orchestrator.rounds import RefineTask
from codeverse.tracks.common import RunContext

log = logging.getLogger(__name__)

SILHOUETTE_GATE = "reference_silhouette"
IOU_REFINE_THRESHOLD = 0.6
FRONT_VIEW_NAMES: tuple[str, ...] = ("front", "front_right_34", "front_left_34")


def target_reference(ctx: RunContext) -> str | None:
    """Path of the reference image to match the silhouette against (role ``target`` first)."""
    refs = [r for r in ctx.spec.references if Path(r.path).is_file()]
    if not refs:
        return None
    return next((r.path for r in refs if r.role == "target"), refs[0].path)


def front_view(renders: RenderSet) -> RenderView | None:
    for name in FRONT_VIEW_NAMES:
        for v in renders.views:
            if v.name == name:
                return v
    return renders.views[0] if renders.views else None


def silhouette_gate(ctx: RunContext, renders: RenderSet) -> GateReport | None:
    """``None`` when the spec has no reference images or nothing could be compared."""
    ref = target_reference(ctx)
    view = front_view(renders)
    if ref is None or view is None:
        return None
    try:
        res = ctx.services.silhouette(view.path, ref)
    except Exception as e:  # noqa: BLE001 — advisory measurement; never fails a round
        log.warning("compare_silhouette failed: %s", e)
        return GateReport(gate=SILHOUETTE_GATE, passed=True, findings=[GateFinding(
            gate=SILHOUETTE_GATE, severity=Severity.INFO, target="overall", message=f"silhouette comparison unavailable: {e}")])
    iou = float(res.get("iou", 0.0)) if isinstance(res, dict) else 0.0
    reliable = bool(res.get("reliable", True)) if isinstance(res, dict) else False
    data = {k: v for k, v in (res.items() if isinstance(res, dict) else []) if isinstance(v, (int, float, str, bool))}
    data["view"] = view.name
    data["reference"] = ref
    low = reliable and iou < IOU_REFINE_THRESHOLD
    msg = (f"front-view outline IoU vs reference = {iou:.3f}" + ("" if reliable else " (unreliable mask)")
           + (f" — below {IOU_REFINE_THRESHOLD:.1f}" if low else ""))
    finding = GateFinding(gate=SILHOUETTE_GATE, severity=Severity.WARN if low else Severity.INFO, target="overall",
                          message=msg, data=data,
                          fix_hint=("match the reference outline: compare proportions (aspect ratio), overall extents and the "
                                    "silhouette of each major part against the reference image" if low else ""))
    return GateReport(gate=SILHOUETTE_GATE, passed=True, findings=[finding])


def silhouette_iou(rec: RoundRecord) -> tuple[float, dict] | None:
    """(iou, data) recorded by ``silhouette_gate`` in a round, if any."""
    for g in rec.gates:
        if g.gate != SILHOUETTE_GATE:
            continue
        for f in g.findings:
            if "iou" in f.data:
                try:
                    return float(f.data["iou"]), dict(f.data)
                except (TypeError, ValueError):
                    return None
    return None


def reference_refine_tasks(ctx: RunContext, last: RoundRecord) -> list[RefineTask]:
    """A priority-1 refine task when the last round's silhouette IoU was below threshold."""
    if not ctx.spec.references:
        return []
    hit = silhouette_iou(last)
    if hit is None:
        return []
    iou, data = hit
    if iou >= IOU_REFINE_THRESHOLD or data.get("reliable") is False:
        return []
    bits = [f"Front-view silhouette IoU vs the reference image is {iou:.2f} (target ≥ {IOU_REFINE_THRESHOLD:.1f})."]
    aspect = data.get("aspect_ratio_err")
    if isinstance(aspect, (int, float)):
        ra, rr = data.get("ref_aspect"), data.get("render_aspect")
        if isinstance(ra, (int, float)) and isinstance(rr, (int, float)):
            bits.append(f"Aspect (w/h): reference {ra:.2f} vs model {rr:.2f} — " +
                        ("make the object wider relative to its height" if rr < ra else "make the object taller relative to its width") + ".")
    bits.append("Re-read the reference: match the outline of every major part (overall proportions first, then part shapes), "
                "keeping names and the plan's dimensions unless the reference clearly disagrees.")
    return [RefineTask(target="overall", kind="reference", instruction=" ".join(bits), priority=1, source="gate")]


__all__ = ["FRONT_VIEW_NAMES", "IOU_REFINE_THRESHOLD", "SILHOUETTE_GATE", "front_view", "reference_refine_tasks",
           "silhouette_gate", "silhouette_iou", "target_reference"]
