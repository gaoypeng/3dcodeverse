"""Deterministic repairs and checks for articulated builds (2026-08-29, phase 5).

Read from compare_art_v4 (14 prompts, 3 arms): with gemini-cli generation the joint sweep
is clean in round 0 for almost every cell, and the judge's remaining *critical*
articulation defects are (a) a joint that moves the wrong way — which
``motion_direction`` already measures, and whose fix is a sign — and (b) a link buried
inside another so it can neither be seen nor move.  Most cells stop on the dollar cap
after one or two rounds, so a fix that costs a round is a fix that mostly never happens.

* :func:`repair_motion_directions` flips the URDF axis of every joint the motion gate
  found reversed, re-measures, keeps the flip when the re-measurement passes and reverts
  it otherwise.  It edits ``src/robot.urdf`` (the agent's file, so later rounds keep it)
  and ``artifacts/robot.urdf`` (what the sweep and the pose sheet read).
* :func:`buried_links` reports a link whose surface lies entirely inside another link's
  watertight mesh at rest.

``CV3D_ART_REPAIRS`` (off unless set; registered in ``plan_features.LIVE_SWITCHES``,
generation-side so an A/B may ``--pin-plan``) turns both on.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from codeverse.config import _call_time_flag
from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.conventions import to_snake

log = logging.getLogger(__name__)

ART_REPAIRS_ENV = "CV3D_ART_REPAIRS"
#: share of a link's sampled surface that must lie inside ONE other link to call it buried
BURIED_FRACTION = 0.98
BURIED_SAMPLES = 200


def repairs_enabled() -> bool:
    return _call_time_flag(ART_REPAIRS_ENV, False)


# ----------------------------------------------------------------------------- axis flip
_JOINT_RE = re.compile(r'(<joint\b[^>]*\bname\s*=\s*"(?P<name>[^"]+)"[^>]*>)(?P<body>.*?)(</joint>)', re.DOTALL)
_AXIS_RE = re.compile(r'<axis\b[^>]*\bxyz\s*=\s*"(?P<xyz>[^"]+)"[^>]*/?>')


def _negate(xyz: str) -> str:
    vals = [float(v) for v in xyz.replace(",", " ").split()]
    out = []
    for v in vals:
        v = -v
        out.append(f"{int(v)}" if float(v).is_integer() else f"{v:g}")
    return " ".join(out)


def flip_joint_axis(urdf_text: str, joint: str) -> tuple[str, str | None]:
    """``(new_text, new_xyz)`` with ``joint``'s ``<axis>`` negated (URDF's default axis
    ``1 0 0`` is written out negated when the element is missing); ``(text, None)`` when
    the joint is not in the file.  Only that joint's block changes."""
    want = to_snake(joint)
    new_xyz: str | None = None

    def _sub(m: re.Match[str]) -> str:
        nonlocal new_xyz
        if to_snake(m.group("name")) != want or new_xyz is not None:
            return m.group(0)
        body = m.group("body")
        am = _AXIS_RE.search(body)
        if am:
            new_xyz = _negate(am.group("xyz"))
            body = body[:am.start("xyz")] + new_xyz + body[am.end("xyz"):]
        else:
            new_xyz = "-1 0 0"
            body = body.rstrip() + f'\n    <axis xyz="{new_xyz}"/>\n  '
        return m.group(1) + body + m.group(4)

    text = _JOINT_RE.sub(_sub, urdf_text)
    return text, new_xyz


def _urdf_files(ws: Any) -> list[Path]:
    return [p for p in (ws.artifacts / "robot.urdf", ws.src / "robot.urdf") if p.is_file()]


def repair_motion_directions(ws: Any, plan: Any, report: GateReport, *, recheck, events=None) -> GateReport:
    """Flip the axis of every joint ``report`` marks reversed, re-measure with ``recheck``
    (``(ws, plan) -> GateReport | None``), keep what now passes and revert the rest.
    Returns the re-measured report with one WARN line per kept flip (the agent must not
    undo it) and the original ERROR for anything reverted."""
    wrong = [f for f in report.errors if f.target]
    files = _urdf_files(ws)
    if not wrong or not files:
        return report
    originals = {p: p.read_text() for p in files}
    flipped: dict[str, str] = {}
    for f in wrong:
        for p in files:
            text, xyz = flip_joint_axis(p.read_text(), f.target)
            if xyz is not None:
                p.write_text(text)
                flipped[f.target] = xyz
    if not flipped:
        return report
    after = recheck(ws, plan)
    ok_now = {f.target for f in (after.findings if after else []) if f.severity != Severity.ERROR}
    still_wrong = {f.target for f in (after.errors if after else [])}
    kept = {j: xyz for j, xyz in flipped.items() if j in ok_now and j not in still_wrong}
    reverted = {j for j in flipped if j not in kept}
    if reverted:
        # put the reverted joints back by flipping them again (their other edits stand)
        for p in files:
            text = p.read_text()
            for j in reverted:
                text, _ = flip_joint_axis(text, j)
            p.write_text(text)
        if not kept:
            for p, t in originals.items():
                p.write_text(t)
            after = recheck(ws, plan) or report
    if events is not None:
        events.emit("repair.motion_flip", kept=kept, reverted=sorted(reverted))
    findings = list((after.findings if after else []) or [])
    for j, xyz in kept.items():
        findings.append(GateFinding(
            gate=report.gate, severity=Severity.WARN, target=j,
            message=f"harness repair: the axis of joint '{j}' in robot.urdf was flipped to ({xyz}) so its motion "
                    f"matches the plan — keep it; do not rewrite this joint's axis or limits",
            data={"kind": "repair", "axis": xyz}))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=report.gate, passed=passed, findings=findings,
                      duration_ms=(after.duration_ms if after else report.duration_ms))


# ----------------------------------------------------------------------------- buried links
def _inside_fraction(body: Any, points_world: np.ndarray) -> float:
    """Share of ``points_world`` inside any of ``body``'s islands (LinkBody keeps geometry in
    the link frame; these bodies were built from world meshes, so T is the identity)."""
    from codeverse.spatial.joints_collide import inside_island

    local = trimesh.transform_points(points_world, body.T_inv)
    mask = np.zeros(len(local), dtype=bool)
    for isl in body.islands:
        mask |= np.asarray(inside_island(isl, local), dtype=bool)
    return float(mask.mean()) if len(mask) else 0.0


def buried_links(robot: Any, *, samples: int = BURIED_SAMPLES, fraction: float = BURIED_FRACTION) -> list[GateFinding]:
    """Links whose surface lies (almost) entirely inside another link at rest.  Uses the
    sweep's own :class:`LinkBody` containment (island-based, works on the open meshes agents
    export) so this check and the collision sweep never disagree about what "inside" means."""
    from codeverse.spatial.joints_collide import LinkBody
    from codeverse.spatial.joints_model import link_world_meshes

    meshes = link_world_meshes(robot, {})
    bodies = {n: LinkBody(n, m) for n, m in meshes.items() if not m.is_empty and m.area > 0}
    out: list[GateFinding] = []
    for name, body in bodies.items():
        pts, _ = trimesh.sample.sample_surface(meshes[name], samples, seed=0)
        lo, hi = meshes[name].bounds
        for other, ob in bodies.items():
            if other == name:
                continue
            olo, ohi = meshes[other].bounds
            if not (np.all(olo <= lo + 1e-6) and np.all(hi <= ohi + 1e-6)):
                continue  # not even inside its box
            try:
                frac = _inside_fraction(ob, pts)
            except Exception:  # noqa: BLE001 — a degenerate mesh; skip, never fail the gate
                continue
            if frac >= fraction:
                out.append(GateFinding(
                    gate="articulation", severity=Severity.ERROR, target=name,
                    message=f"link '{name}' lies entirely inside '{other}' at rest ({frac * 100:.0f}% of its surface): "
                            f"it can neither be seen nor move",
                    fix_hint=f"move '{name}' outside '{other}' (or cut a pocket in '{other}' where it sits) so the part "
                             f"is visible in the rest pose; if it is really internal, merge it into '{other}'",
                    data={"kind": "buried", "inside": other, "fraction": round(frac, 3)}))
                break
    return out


__all__ = ["ART_REPAIRS_ENV", "BURIED_FRACTION", "buried_links", "flip_joint_axis", "repair_motion_directions",
           "repairs_enabled"]
