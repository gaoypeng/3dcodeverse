"""``JudgeInput`` and the one place a round becomes one (``round_input``), plus the pure replay helpers."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import GateReport, Judgment, Measurement, RenderSet
from codeverse3d.contracts.common import TRACK_INFO, Track
from codeverse3d.contracts.plan import (
    AcceptanceItem,
    ArticulatedPlan,
    GraphicsPlan,
    Plan,
    ScenePlan,
    StaticPlan,
)
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import to_authoring_frame
from codeverse3d.workspace import Workspace

#: tracks whose rounds have one canonical GLB the judge slice channel may cut
SLICE_TRACKS = ("static_object", "articulated_object")
#: rubric for a static object judged against reference images (adds the silhouette criterion)
REFERENCE_RUBRIC = "reference"


def run_rubric(spec: Spec, default: str = "") -> str:
    """The rubric a run of ``spec`` is judged on: :data:`REFERENCE_RUBRIC` for a static object with
    references, else ``default`` or the track's own."""
    if spec.references and spec.track is Track.STATIC_OBJECT:
        return REFERENCE_RUBRIC
    return default or TRACK_INFO[spec.track].rubric


class JudgeInput(BaseModel):
    spec: Spec
    renders: RenderSet
    measurement: Measurement | None = None
    gates: list[GateReport] = Field(default_factory=list)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    plan_summary: str = Field(default="", description="short plan digest (parts/zones/joints) for grounding")
    part_names: list[str] = Field(default_factory=list, description="an object plan's part names (the reference diff)")
    round_index: int = 0
    previous: Judgment | None = Field(default=None, description="last verdict (for delta framing)")
    extra_context: str = ""
    geometry_views: RenderSet | None = Field(
        default=None, description="clay/normals renders for the geometry-only montage (holes, intersections)"
    )
    glb_path: str | None = Field(
        default=None,
        description="the round's canonical GLB (object tracks fill it; `3dcode judge` fills it from the stored "
        "build) — the D48 slice channel cuts it on gate-ERROR rounds; None disables the channel",
    )


def plan_summary(plan: Plan | None, language: str) -> str:
    """The plan digest the judge reads.  An object's size is written W×H×D in the GLB frame (Y-up),
    so a Z-up plan's (W, D, H) is permuted first."""
    if isinstance(plan, GraphicsPlan):
        passes = ", ".join(f"{p.name} ({p.kind})" for p in plan.passes)
        return (f"{plan.title}: {plan.summary} Style: {plan.style}. Passes: {passes}. Key visuals: {'; '.join(plan.key_visuals)}. "
                f"Motion: {plan.motion or '(none planned)'}. {plan.resolution[0]}x{plan.resolution[1]}, loop {plan.duration_s:g}s.")
    if isinstance(plan, ScenePlan):
        return (f"{plan.title}: {plan.summary} Setting: {plan.setting}. Zones: {', '.join(z.name for z in plan.zones)}. "
                f"Assets: {', '.join(a.name for a in plan.assets)}. Cameras: {', '.join(c.name for c in plan.cameras)}.")
    if not isinstance(plan, StaticPlan):
        return ""
    parts = ", ".join(f"{p.name}×{p.instances}" if p.instances > 1 else p.name for p in plan.parts)
    e = to_authoring_frame(plan.overall_bbox.extents, language, extents=True)
    text = f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m (W×H×D). Parts: {parts}."
    if isinstance(plan, ArticulatedPlan):
        joints = "; ".join(f"{j.name} ({j.type} {j.parent}→{j.child}, [{j.lower:.2f},{j.upper:.2f}])" for j in plan.joints)
        text += f" Root link {plan.root_link}. Joints: {joints}."
    return text


def round_input(spec: Spec, plan: Plan | None, rnd: RoundRecord, *, renders: RenderSet, gates: Sequence[GateReport],
                previous: Judgment | None, extra_context: str, geometry_views: RenderSet | None,
                glb_path: str | None) -> JudgeInput:
    """The judge payload of one round, built from the round in hand or replayed from disk."""
    return JudgeInput(
        spec=spec, renders=renders, measurement=rnd.measurement, gates=list(gates),
        acceptance=list(getattr(plan, "acceptance", None) or []), plan_summary=plan_summary(plan, spec.language),
        part_names=[p.name for p in plan.parts] if isinstance(plan, StaticPlan) else [],
        round_index=rnd.index, previous=previous, extra_context=extra_context, geometry_views=geometry_views,
        glb_path=glb_path if spec.track.value in SLICE_TRACKS else None,
    )


def resolve_paths(ws: Workspace, rs: RenderSet | None) -> RenderSet | None:
    """Render paths out of a round record, resolved against THIS workspace."""
    if rs is None:
        return None
    fixed = [v.model_copy(update={"path": str(ws.rebase(v.path))}) for v in rs.views]
    sheet = str(ws.rebase(rs.contact_sheet)) if rs.contact_sheet else rs.contact_sheet
    out_dir = str(ws.rebase(rs.out_dir)) if rs.out_dir else rs.out_dir
    return rs.model_copy(update={"views": fixed, "contact_sheet": sheet, "out_dir": out_dir})


def judged_subset(rs: RenderSet | None) -> RenderSet | None:
    """The views the in-run judge saw (per-view ``judge`` flags); rounds without flags keep every view."""
    if rs is None or not any(v.judge is not None for v in rs.views):
        return rs
    return rs.model_copy(update={"views": [v for v in rs.views if v.judge]})
