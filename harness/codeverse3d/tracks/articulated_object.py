"""ArticulatedObjectTrack: the static pipeline + URDF pose sweep + pose views.

Language: urdf_blender (bpy link meshes + hand-written URDF).  After the build
the harness loads ``artifacts/robot.urdf``, samples joint poses, sweeps for
penetrations / floating links (→ gate ERRORS with concrete fix hints) and
renders a pose sheet which is appended to the judge's views.
"""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import TRACK_INFO, Track
from codeverse3d.contracts.plan import ArticulatedPlan, Plan
from codeverse3d.conventions import to_snake
from codeverse3d.spatial.render import RenderError
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.static_object import ObjectPipeline, StaticObjectTrack
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

SWEEP_GATE = "joint_sweep"


class ArticulatedPipeline(ObjectPipeline):
    """Object gates + joint sweep; object views + pose views."""

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        out = super().gates(ctx, round_index, build, measurement)
        repaired = self._axis_repair(ctx)
        out_dir = ctx.ws.renders_dir(round_index) / "poses"
        report, views = ctx.services.joint_sweep(ctx.ws, ctx.plan, out_dir)
        ctx.extra["pose_views"] = views
        out.append(report)
        motion = self._motion_gate(ctx)
        if motion is not None:
            if repaired:
                motion.findings.append(GateFinding(
                    gate=MOTION_GATE, severity=Severity.INFO, target=", ".join(repaired),
                    message=(f"harness axis repair rewrote <axis xyz> of {', '.join(repaired)} in src/robot.urdf "
                             "(measured motion was provably wrong) — build on it, do not undo it")))
            out.append(motion)
        return out

    @staticmethod
    def _axis_repair(ctx: RunContext) -> list[str]:
        """Measure → deterministically fix wrong joint axes BEFORE the sweep renders,
        so this round's poses, renders and judge all see the corrected motion."""
        if os.environ.get(AXIS_REPAIR_ENV) == "0":
            return []
        try:
            pre = ctx.services.motion_checks(ctx.ws, ctx.plan)
            repaired = repair_motion_axes(ctx.ws, pre)
        except Exception as e:  # noqa: BLE001 — advisory repair; the gate still reports truth
            log.warning("axis repair skipped: %s", e)
            return []
        if repaired:
            ctx.events.emit("gate.axis_repaired", joints=repaired)
        return repaired

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


# ----------------------------------------------------------------------------- sweep adapter
def default_joint_sweep(ws: Workspace, plan: Plan | None, out_dir: Path) -> tuple[GateReport, list[RenderView]]:
    # ``plan`` is unread HERE — the sweep reads the built URDF off disk — but it stays in
    # the Services.joint_sweep signature: it is the interface's information, and the test
    # double (tests/orchestrator_tracks/fakes.py) synthesises its poses and findings from
    # plan.joints because it has no URDF to read.
    """Adapter to ``codeverse3d.spatial.joints``: load → pose samples →
    ``sweep_collisions`` → ``sweep_findings`` (gate) and ``render_poses`` (pose views +
    ``articulation_sheet.png`` for the judge)."""
    from codeverse3d.spatial import joints

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
    findings = [f.model_copy(update={"gate": SWEEP_GATE})
                for f in joints.aggregate_findings(joints.sweep_findings(report))]
    try:
        findings += [f.model_copy(update={"gate": SWEEP_GATE}) for f in joints.buried_links(robot)]
    except Exception as e:  # noqa: BLE001 — an extra check never fails the gate
        log.warning("buried-link check failed: %s", e)
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


#: kill-switch for the deterministic axis repair (default ON, mirrors C3D_CAMERA_REPAIR)
AXIS_REPAIR_ENV = "C3D_AXIS_REPAIR"


def _set_axis_in_urdf_text(text: str, urdf_joint: str, axis: tuple[float, float, float]) -> str | None:
    """``text`` with joint ``urdf_joint``'s ``<axis xyz>`` replaced (inserted when
    missing).  String surgery instead of an XML round-trip so authored comments and
    formatting survive; ``None`` when the joint block cannot be located safely."""
    xyz = f"{axis[0]:g} {axis[1]:g} {axis[2]:g}"
    start = -1
    pos = 0
    while (jpos := text.find("<joint", pos)) != -1:
        head_end = text.find(">", jpos)
        if head_end == -1:
            return None
        head = text[jpos:head_end]
        if f'name="{urdf_joint}"' in head or f"name='{urdf_joint}'" in head:
            start = jpos
            break
        pos = jpos + 6
    if start == -1:
        return None
    end = text.find("</joint>", start)
    if end == -1:
        return None
    block = text[start:end]
    a0 = block.find("<axis")
    if a0 != -1:
        gt = block.find(">", a0)
        if gt == -1:
            return None
        if block[gt - 1] == "/":                       # <axis .../>
            axis_end = gt + 1
        else:                                          # <axis ...>...</axis> — "/>" never occurs
            close_pair = block.find("</axis>", gt)     # in "</axis>", so searching for it would
            if close_pair == -1:                       # land on the NEXT self-closing tag and
                return None                            # splice away the joint's <limit/>
            axis_end = close_pair + len("</axis>")
        new_block = block[:a0] + f'<axis xyz="{xyz}"/>' + block[axis_end:]
    else:
        head_end = block.find(">")
        if head_end == -1:
            return None
        new_block = block[: head_end + 1] + f'\n    <axis xyz="{xyz}"/>' + block[head_end + 1:]
    return text[:start] + new_block + text[start + len(block):]


def repair_motion_axes(ws: Workspace, report: GateReport | None) -> list[str]:
    """Deterministically rewrite provably-wrong ``<axis xyz>`` in ``src/robot.urdf``.

    For every motion_direction ERROR whose measured data admits an exact fix:
    anti-parallel motion (``cos <= -0.5``) negates the authored axis; otherwise a
    computed ``suggested_axis`` is written verbatim.  The same fixes rode along as
    fix_hints for a whole battery and the agents applied none of them
    (ax_metronome / ax_swiss_knife, 2026-08-31): the measurement exists, so the
    execution goes deterministic.  ``C3D_AXIS_REPAIR=0`` disables.  Both the
    authored file and the built ``artifacts/robot.urdf`` copy are updated so the
    re-run gate and the pose sweep see the repair.  Returns repaired plan-joint
    names; a finding without measured data (``cos``) is never touched."""
    if report is None or os.environ.get(AXIS_REPAIR_ENV) == "0":
        return []
    src = ws.src / "robot.urdf"
    if not src.is_file():
        return []
    text = src.read_text()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    joints: dict[str, ET.Element] = {j.get("name", ""): j for j in root.findall("joint")}
    by_snake = {to_snake(n): n for n in joints}
    repaired: list[str] = []
    for f in report.findings:
        if f.severity != Severity.ERROR:
            continue
        data = f.data or {}
        cos = data.get("cos")
        if cos is None:
            continue
        urdf_name = by_snake.get(to_snake(f.target or ""))
        if urdf_name is None:
            continue
        if cos <= -0.5:
            ax_el = joints[urdf_name].find("axis")
            cur = [float(v) for v in (ax_el.get("xyz") or "1 0 0").split()] if ax_el is not None else [1.0, 0.0, 0.0]
            target_axis = (-cur[0], -cur[1], -cur[2])
        elif data.get("suggested_axis"):
            sx, sy, sz = data["suggested_axis"]
            target_axis = (float(sx), float(sy), float(sz))
        else:
            continue
        new_text = _set_axis_in_urdf_text(text, urdf_name, target_axis)
        if new_text is None:
            continue
        try:  # the repair must never ship a file it cannot prove well-formed and applied
            got = ET.fromstring(new_text).findall("joint")
            el = next(j for j in got if j.get("name") == urdf_name).find("axis")
            assert el is not None
            vals = [float(v) for v in el.get("xyz").split()]
            assert all(abs(a - b) < 1e-9 for a, b in zip(vals, target_axis, strict=True))
        except Exception:
            continue
        text = new_text
        repaired.append(f.target or urdf_name)
    if repaired:
        src.write_text(text)
        art = ws.artifacts / "robot.urdf"
        if art.is_file():
            art.write_text(text)
    return repaired


def default_motion_checks(ws: Workspace, plan: Plan | None) -> GateReport | None:
    """Run ``motion_direction_check`` for every plan joint with an obvious motion
    direction.  ``None`` when the plan has no such joints; unknown joints /
    missing meshes are WARN findings (the sweep gate already reports them)."""
    joints = list(getattr(plan, "joints", None) or [])
    wanted = [(j, expected_direction(j.motion)) for j in joints]
    wanted = [(j, d) for j, d in wanted if d is not None]
    if not wanted:
        return None
    from codeverse3d.spatial import joints as sj

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
        data = {"expected": expected, "observed_dir": list(chk.observed_dir), "motion": j.motion,
                "cos": chk.cos, "suggested_axis": list(chk.suggested_axis) if chk.suggested_axis else None}
        if chk.ok:
            findings.append(GateFinding(gate=MOTION_GATE, severity=Severity.INFO, target=j.name, message=chk.message, data=data))
        else:
            # anti-parallel: the sign is provably wrong — negate.  Orthogonal (the
            # af_swiss_knife class: three rounds of guessing on the same joints): the
            # geometry admits an exact axis, so the hint states it verbatim.
            if chk.cos >= -0.5 and chk.suggested_axis is not None:
                sx, sy, sz = chk.suggested_axis
                hint = (f"in src/robot.urdf set joint '{j.name}' <axis xyz=\"{sx:g} {sy:g} {sz:g}\"/> "
                        f"— computed from the pivot so positive motion goes {expected}")
            else:
                hint = axis_fix_hint(j.name, getattr(j, "axis", None), expected)
            findings.append(GateFinding(
                gate=MOTION_GATE, severity=Severity.ERROR, target=j.name,
                message=f"{chk.message}. Plan says: \"{j.motion}\"",
                fix_hint=hint, data=data))
    return GateReport(gate=MOTION_GATE, passed=not any(f.severity == Severity.ERROR for f in findings),
                      findings=findings, duration_ms=int((time.time() - t0) * 1000))
