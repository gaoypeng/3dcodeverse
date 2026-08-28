"""ArticulatedObjectTrack: the static pipeline + URDF pose sweep + pose views.

Language: urdf_blender (bpy link meshes + hand-written URDF).  After the build
the harness loads ``artifacts/robot.urdf``, samples joint poses, sweeps for
penetrations / floating links (→ gate ERRORS with concrete fix hints) and
renders a pose sheet which is appended to the judge's views.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
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
from codeverse.conventions import to_snake
from codeverse.spatial.render import RenderError
from codeverse.tracks.common import RunContext
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
    # ``plan`` is unread HERE — the sweep reads the built URDF off disk — but it stays in
    # the Services.joint_sweep signature: it is the interface's information, and the test
    # double (tests/orchestrator_tracks/fakes.py) synthesises its poses and findings from
    # plan.joints because it has no URDF to read.
    """Adapter to ``codeverse.spatial.joints``: load → pose samples →
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
    try:
        for label, rs in joints.render_poses(robot, out_dir):
            if rs.views:
                v = rs.views[0]
                views.append(RenderView(name=f"pose_{label}", path=v.path, mode=v.mode, width=v.width, height=v.height))
    except RenderError as e:
        # The pose renders are the judge's evidence, not the gate's: the collision sweep
        # above is already measured.  A render timeout here (art_verify architect_lamp,
        # 2026-08-26: 330 s in render_glb.mjs with three articulated runs sharing the
        # browser) used to raise out of gates() and fail the run before its first round.
        log.warning("pose renders failed; the sweep gate stands without a sheet: %s", e)
        gate.findings.append(GateFinding(gate=SWEEP_GATE, severity=Severity.WARN, target="poses",
                                         message=f"pose renders failed ({str(e)[:160]}); the judge sees no articulation sheet this round",
                                         fix_hint="nothing to fix in the code: a render timeout under load"))
    sheet = out_dir / joints.ARTICULATION_SHEET_NAME
    if sheet.is_file():
        views.insert(0, RenderView(name="articulation_sheet", path=str(sheet), mode="shaded"))
    return gate, views


def _find_urdf(ws: Workspace) -> Path | None:
    for cand in (ws.artifacts / "robot.urdf", ws.src / "robot.urdf"):
        if cand.is_file():
            return cand
    return None


# ===================================================================== planned-motion gate
# (merged from codeverse/tracks/motion.py, 2026-08-28 — its two importers were this file
#  and one lazy Services hook; the byte-identical _find_urdf duplicate died with it)
MOTION_GATE = "motion_direction"

#: phrase → direction key understood by ``spatial.joints.motion_direction_check``.
#: Longer phrases first so "pulls out" wins over "out"; explicit axes first of all.
_PHRASES: tuple[tuple[str, str], ...] = (
    ("-y", "-y"), ("+y", "+y"), ("-z", "-z"), ("+z", "+z"), ("-x", "-x"), ("+x", "+x"),
    ("towards the front", "front"), ("toward the front", "front"), ("to the front", "front"),
    ("opens to the front", "front"), ("opens out", "front"), ("pulls out", "front"), ("slides out", "front"),
    ("pull out", "front"), ("slide out", "front"), ("swings out", "front"), ("outward", "front"), ("forward", "front"),
    ("towards the viewer", "front"), ("toward the viewer", "front"), ("frontward", "front"),
    ("towards the back", "back"), ("toward the back", "back"), ("to the back", "back"), ("pushes in", "back"),
    ("slides in", "back"), ("backward", "back"), ("rearward", "back"), ("inward", "back"),
    ("upward", "up"), ("upwards", "up"), ("opens up", "up"), ("lifts", "up"), ("lift", "up"), ("raises", "up"),
    ("raise", "up"), ("rises", "up"),
    ("downward", "down"), ("downwards", "down"), ("lowers", "down"), ("drops", "down"),
    ("folds down", "down"), ("opens down", "down"),
    ("to the left", "left"), ("leftward", "left"), ("swings left", "left"),
    ("to the right", "right"), ("rightward", "right"), ("swings right", "right"),
    ("front", "front"), ("back", "back"), ("up", "up"), ("down", "down"), ("left", "left"), ("right", "right"),
    ("out", "front"),
)

_SKIP_MARKERS = ("not ", "n't", "either", "both", "around", "rotates about", "spins")
_DIR_GROUPS = {"front": "y", "back": "y", "-y": "y", "+y": "y", "up": "z", "down": "z", "+z": "z", "-z": "z",
               "left": "x", "right": "x", "-x": "x", "+x": "x"}


def expected_direction(motion: str) -> str | None:
    """Direction key for an obvious motion description, else ``None``.

    Rules: explicit signed axes win; otherwise the first (longest) phrase hit;
    text naming two different axes ("up and to the left"), negations or pure
    rotations ("spins around its axis") → ``None`` (ambiguous — skip)."""
    text = " ".join((motion or "").lower().replace("_", " ").split())
    if not text or any(m in text for m in _SKIP_MARKERS):
        return None
    padded = f" {text} "
    hits: list[str] = []
    for phrase, key in _PHRASES:
        if _contains_word(padded, phrase):
            hits.append(key)
            if key in ("-y", "+y", "-z", "+z", "-x", "+x"):
                return key
    if not hits:
        return None
    axes = {_DIR_GROUPS[h] for h in hits}
    if len(axes) > 1:
        # "opens up and out" — up/front conflict: the longest phrase decides only if one axis dominates
        first_axis = _DIR_GROUPS[hits[0]]
        if any(_DIR_GROUPS[h] != first_axis for h in hits[1:]):
            return None
    return hits[0]


def _contains_word(padded: str, phrase: str) -> bool:
    """Whole-word containment (phrase boundaries are spaces or punctuation)."""
    i = padded.find(phrase)
    while i >= 0:
        before = padded[i - 1] if i > 0 else " "
        after = padded[i + len(phrase)] if i + len(phrase) < len(padded) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = padded.find(phrase, i + 1)
    return False


def axis_fix_hint(joint_name: str, axis: Sequence[float] | None, expected: str) -> str:
    if axis is not None and len(axis) == 3:
        neg = tuple(-float(a) for a in axis)
        return (f"in src/robot.urdf set joint '{joint_name}' <axis xyz=\"{neg[0]:g} {neg[1]:g} {neg[2]:g}\"/> "
                f"(negate the axis) — or swap lower/upper so positive motion goes {expected}")
    return f"negate the <axis> of joint '{joint_name}' in src/robot.urdf (or swap lower/upper) so motion goes {expected}"


def default_motion_checks(ws: Workspace, plan: Plan | None) -> GateReport | None:
    """Run ``motion_direction_check`` for every plan joint with an obvious motion
    direction.  ``None`` when the plan has no such joints; unknown joints /
    missing meshes are WARN findings (the sweep gate already reports them)."""
    joints = list(getattr(plan, "joints", None) or [])
    wanted = [(j, expected_direction(j.motion)) for j in joints]
    wanted = [(j, d) for j, d in wanted if d is not None]
    if not wanted:
        return None
    from codeverse.spatial import joints as sj

    urdf = _find_urdf(ws)
    if urdf is None:
        return None
    t0 = time.time()
    findings: list[GateFinding] = []
    try:
        robot = sj.load_urdf(urdf, ws.artifacts / "meshes")
    except sj.UrdfError as e:
        return GateReport(gate=MOTION_GATE, passed=True, findings=[GateFinding(
            gate=MOTION_GATE, severity=Severity.WARN, target="robot.urdf", message=f"motion checks skipped: {e}")])
    urdf_names = {to_snake(n): n for n in getattr(robot, "joints", {})}
    for j, expected in wanted:
        name = urdf_names.get(to_snake(j.name), j.name)  # URDF joint named like the plan joint (any casing)
        try:
            chk = sj.motion_direction_check(robot, name, expected)
        except sj.UrdfError as e:
            findings.append(GateFinding(gate=MOTION_GATE, severity=Severity.WARN, target=j.name,
                                        message=f"motion check skipped: {e}", data={"expected": expected}))
            continue
        data = {"expected": expected, "observed_dir": list(chk.observed_dir), "motion": j.motion}
        if chk.ok:
            findings.append(GateFinding(gate=MOTION_GATE, severity=Severity.INFO, target=j.name, message=chk.message, data=data))
        else:
            findings.append(GateFinding(
                gate=MOTION_GATE, severity=Severity.ERROR, target=j.name,
                message=f"{chk.message}. Plan says: \"{j.motion}\"",
                fix_hint=axis_fix_hint(j.name, getattr(j, "axis", None), expected), data=data))
    return GateReport(gate=MOTION_GATE, passed=not any(f.severity == Severity.ERROR for f in findings),
                      findings=findings, duration_ms=int((time.time() - t0) * 1000))
