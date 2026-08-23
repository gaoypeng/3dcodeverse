"""ArticulatedObjectTrack: the static pipeline + URDF pose sweep + pose views.

Language: urdf_blender (bpy link meshes + hand-written URDF).  After the build
the harness loads ``artifacts/robot.urdf``, samples joint poses, sweeps for
penetrations / floating links (→ gate ERRORS with concrete fix hints) and
renders a pose sheet which is appended to the judge's views.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse.contracts.common import TRACK_INFO, Track
from codeverse.contracts.plan import ArticulatedPlan, Plan
from codeverse.tracks.common import RunContext
from codeverse.tracks.motion import MOTION_GATE
from codeverse.tracks.static_object import ObjectPipeline, StaticObjectTrack
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

SWEEP_GATE = "joint_sweep"


class ArticulatedPipeline(ObjectPipeline):
    """Object gates + joint sweep; object views + pose views."""

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        out = super().gates(ctx, round_index, build, measurement)
        out_dir = ctx.ws.renders_dir(round_index) / "poses"
        report, views = ctx.services.joint_sweep(ctx.ws, ctx.plan, out_dir)
        ctx.extra["pose_views"] = views
        out.append(report)
        motion = self._motion_gate(ctx)
        if motion is not None:
            out.append(motion)
        return out

    @staticmethod
    def _motion_gate(ctx: RunContext) -> GateReport | None:
        """Planned motion text ("pulls out to the front") vs the URDF's real direction."""
        try:
            return ctx.services.motion_checks(ctx.ws, ctx.plan)
        except Exception as e:  # noqa: BLE001 — advisory gate; the sweep gate is the hard one
            log.warning("motion direction checks failed: %s", e)
            ctx.events.emit("gate.motion_failed", error=f"{type(e).__name__}: {e}")
            return None

    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet:
        rs = super().render(ctx, round_index, build, measurement)
        pose_views: list[RenderView] = ctx.extra.pop("pose_views", []) or []
        if pose_views:
            rs.views = list(rs.views) + pose_views
        return rs

    def plan_summary(self, ctx: RunContext) -> str:
        base = super().plan_summary(ctx)
        plan = ctx.plan
        if not isinstance(plan, ArticulatedPlan):
            return base
        joints = "; ".join(f"{j.name} ({j.type} {j.parent}→{j.child}, [{j.lower:.2f},{j.upper:.2f}])" for j in plan.joints)
        return f"{base} Root link {plan.root_link}. Joints: {joints}."

    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        report = next((g for g in gates if g.gate == SWEEP_GATE), None)
        motion = next((g for g in gates if g.gate == MOTION_GATE), None)
        lines = ["Articulation sheet: the pose_* views show the object at rest, each joint at its lower and upper limit."]
        if report is not None:
            errs = [f"- {f.target or 'joint'}: {f.message}" for f in report.errors]
            lines.append(f"Joint sweep: {'no penetrations' if not errs else str(len(errs)) + ' problems'}")
            lines.extend(errs[:10])
        if motion is not None:
            wrong = [f"- {f.target}: {f.message}" for f in motion.errors]
            lines.append("Motion direction (harness FK check): " + ("all planned directions realised" if not wrong else f"{len(wrong)} WRONG"))
            lines.extend(wrong[:6])
        return "\n".join(lines)


class ArticulatedObjectTrack(StaticObjectTrack):
    track = Track.ARTICULATED_OBJECT
    rubric = TRACK_INFO[Track.ARTICULATED_OBJECT].rubric
    plan_model = ArticulatedPlan
    generate_template = "tracks/generate_articulated.j2"

    def make_pipeline(self) -> ArticulatedPipeline:
        return ArticulatedPipeline()

    def system_prompt(self, ctx: RunContext) -> str:
        return ("You are an expert in Blender bpy and URDF writing RAW code (no SDKs). Links are bpy objects named exactly "
                "as the URDF links; joints are native URDF with origins/axes in the parent link frame. Exact numbers beat adjectives.")


# ----------------------------------------------------------------------------- sweep adapter
def default_joint_sweep(ws: Workspace, plan: Plan | None, out_dir: Path) -> tuple[GateReport, list[RenderView]]:
    """Adapter to ``codeverse.spatial.joints`` (package E): load → pose samples →
    ``sweep_collisions`` → ``sweep_findings`` (gate) and ``render_poses`` (pose views +
    ``articulation_sheet.png`` for the judge)."""
    from codeverse.spatial import joints

    urdf = _find_urdf(ws)
    if urdf is None:
        return GateReport(gate=SWEEP_GATE, passed=False, findings=[GateFinding(
            gate=SWEEP_GATE, severity=Severity.ERROR, target="robot.urdf", message="no robot.urdf found after build",
            fix_hint="write src/robot.urdf (native URDF) referencing meshes/<link>.glb for every link")]), []
    t0 = time.time()
    try:
        robot = joints.load_urdf(urdf, ws.artifacts / "meshes")
        report = joints.sweep_collisions(robot, joints.pose_samples(robot))
    except joints.UrdfError as e:
        return GateReport(gate=SWEEP_GATE, passed=False, findings=[GateFinding(
            gate=SWEEP_GATE, severity=Severity.ERROR, target="robot.urdf", message=f"URDF sweep failed: {e}",
            fix_hint="fix robot.urdf so every link has a mesh under meshes/<link>.glb and joints form one tree")]), []
    findings = [f.model_copy(update={"gate": SWEEP_GATE}) for f in joints.sweep_findings(report)]
    gate = GateReport(gate=SWEEP_GATE, passed=not any(f.severity == Severity.ERROR for f in findings), findings=findings,
                      duration_ms=int((time.time() - t0) * 1000))
    views: list[RenderView] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, rs in joints.render_poses(robot, out_dir):
        if rs.views:
            v = rs.views[0]
            views.append(RenderView(name=f"pose_{label}", path=v.path, mode=v.mode, width=v.width, height=v.height))
    sheet = out_dir / joints.ARTICULATION_SHEET_NAME
    if sheet.is_file():
        views.insert(0, RenderView(name="articulation_sheet", path=str(sheet), mode="shaded"))
    return gate, views


def _find_urdf(ws: Workspace) -> Path | None:
    for cand in (ws.artifacts / "robot.urdf", ws.src / "robot.urdf"):
        if cand.is_file():
            return cand
    return None
