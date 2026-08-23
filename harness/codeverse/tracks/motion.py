"""Planned joint motion → expected direction → deterministic check on the built URDF.

The planner writes one line per joint ("drawer pulls out towards -Y", "lid
opens upward", "door swings to the left").  When that text names an obvious
direction we map it to one of ``codeverse.spatial.joints`` direction keys
(Z-up, -Y front — the URDF/Blender authoring frame) and ask
``motion_direction_check`` whether the child link really moves that way when
the joint moves positively from rest.  A wrong direction is a gate ERROR with
the concrete fix (negate the axis / swap the limits); ambiguous or silent text
is skipped — this gate never guesses.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.contracts.plan import Plan
from codeverse.conventions import to_snake
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

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


def _find_urdf(ws: Workspace) -> Path | None:
    for cand in (ws.artifacts / "robot.urdf", ws.src / "robot.urdf"):
        if cand.is_file():
            return cand
    return None


__all__ = ["MOTION_GATE", "axis_fix_hint", "default_motion_checks", "expected_direction"]
