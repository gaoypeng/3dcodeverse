"""``JudgeInput``, the ONE way a round becomes one (``round_input`` + ``plan_summary``: the in-run
judge and every replay of a stored round build it here) and the pure replay helpers."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import GateReport, Judgment, Measurement, RenderSet
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

#: tracks whose rounds have one canonical GLB the judge slice channel (D48) may cut;
#: scene and graphics have none and are unaffected.
SLICE_TRACKS = ("static_object", "articulated_object")


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


# ===================================================================== one round → one JudgeInput
def plan_summary(plan: Plan | None, language: str) -> str:
    """The plan digest the judge reads, for every track.  An object's overall size is written
    W×H×D in the GLB frame (Y-up) — the frame of the measurement table beside it — so a Z-up
    plan's (W, D, H) is permuted first.  Four track methods and a fifth dict digest for the
    replays used to write this: a replayed Z-up object read W×D×H, and a replayed shader lost
    its style, passes, key visuals and motion."""
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
    """The judge payload of one round.  The in-run judge (``tracks/steps._judge``) builds it from the
    round in hand, every replay of a stored round (``3dcode judge``, calibration:
    ``cli/_judge.build_judge_input``) from disk — so a replay reads what the in-run judge read."""
    return JudgeInput(
        spec=spec, renders=renders, measurement=rnd.measurement, gates=list(gates),
        acceptance=list(getattr(plan, "acceptance", None) or []), plan_summary=plan_summary(plan, spec.language),
        part_names=[p.name for p in plan.parts] if isinstance(plan, StaticPlan) else [],
        round_index=rnd.index, previous=previous, extra_context=extra_context, geometry_views=geometry_views,
        # D48: the round's canonical GLB feeds the conditional slice channel (object tracks only)
        glb_path=glb_path if spec.track.value in SLICE_TRACKS else None,
    )


# ===================================================================== round replay
def resolve_paths(ws: Workspace, rs: RenderSet | None) -> RenderSet | None:
    """Render paths out of a round record, resolved against THIS workspace.

    Delegates to ``Workspace.rebase``: this used to rebase only paths for which
    ``is_absolute()`` was False, which made it a no-op against every record the harness
    itself writes (they are all absolute) — so a moved or archived run kept pointing at
    the host it was produced on.
    """
    if rs is None:
        return None
    fixed = [v.model_copy(update={"path": str(ws.rebase(v.path))}) for v in rs.views]
    sheet = str(ws.rebase(rs.contact_sheet)) if rs.contact_sheet else rs.contact_sheet
    out_dir = str(ws.rebase(rs.out_dir)) if rs.out_dir else rs.out_dir
    return rs.model_copy(update={"views": fixed, "contact_sheet": sheet, "out_dir": out_dir})


def judged_subset(rs: RenderSet | None) -> RenderSet | None:
    """The views the in-run judge actually saw: the per-view ``judge`` flags
    stamped at render time; legacy rounds (no flags) keep every stored view."""
    if rs is None or not any(v.judge is not None for v in rs.views):
        return rs
    return rs.model_copy(update={"views": [v for v in rs.views if v.judge]})
