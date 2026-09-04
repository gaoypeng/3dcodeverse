"""Deterministic pose sampling for sweeps and articulation sheets."""

from __future__ import annotations

import math

import numpy as np

from codeverse.spatial.joints_model import Joint, Robot


def _joint_values(j: Joint) -> list[float]:
    if j.type == "continuous":
        return [0.0, -math.pi / 2, math.pi / 2, math.pi]
    assert j.lower is not None and j.upper is not None
    vals = [0.0, j.lower, 0.5 * (j.lower + j.upper), j.upper]
    out: list[float] = []
    for v in vals:
        if all(abs(v - o) > 1e-9 for o in out):
            out.append(v)
    return out


def pose_samples(robot: Robot, n_random: int = 8, seed: int = 0) -> list[dict[str, float]]:
    """Deterministic pose set: rest, each joint at lower/mid/upper (others at 0),
    then ``n_random`` seeded random combinations within limits.
    Continuous joints sample 0, ±π/2, π and random in [-π, π]."""
    # only the degrees of freedom are driven: a mimicking joint follows its source
    # through fk, so sampling it independently would pose a coupled mechanism in a
    # state the mechanism cannot reach
    movable = robot.independent_joints()
    poses: list[dict[str, float]] = [{}]
    for j in movable:
        for v in _joint_values(j):
            if abs(v) < 1e-12:
                continue
            poses.append({j.name: v})
    rng = np.random.default_rng(seed)
    for _ in range(n_random if len(movable) > 1 else 0):
        q: dict[str, float] = {}
        for j in movable:
            if j.type == "continuous":
                q[j.name] = float(rng.uniform(-math.pi, math.pi))
            else:
                assert j.lower is not None and j.upper is not None
                q[j.name] = float(rng.uniform(j.lower, j.upper))
        poses.append(q)
    return poses


def pose_label(robot: Robot, q: dict[str, float]) -> str:
    """Short deterministic label: ``rest`` · ``hinge@upper`` · ``hinge=0.52,slide=0.10``."""
    active = {k: v for k, v in q.items() if abs(v) > 1e-12}
    if not active:
        return "rest"
    if len(active) == 1:
        (name, v), = active.items()
        j = robot.joints.get(name)
        if j is not None:
            for tag, ref in (("lower", j.lower), ("upper", j.upper)):
                if ref is not None and abs(v - ref) < 1e-9:
                    return f"{name}@{tag}"
            if j.lower is not None and j.upper is not None and abs(v - 0.5 * (j.lower + j.upper)) < 1e-9:
                return f"{name}@mid"
        return f"{name}={v:.3g}"
    return ",".join(f"{k}={v:.3g}" for k, v in sorted(active.items()))


def limit_poses(robot: Robot) -> list[tuple[str, dict[str, float]]]:
    """``rest`` + every INPUT joint at its lower and upper limit (deduped) — the pose set
    behind the articulation contact sheet and ``joint_sweep(joints=...)``.

    Independent joints only, for the same reason as :func:`pose_samples`: ``fk`` resolves a
    driven joint from the one it follows and ignores a value handed in for it, so a tile
    labelled ``<driven>@upper`` would render the rest pose under a label that says it moved
    (an 8-rib umbrella: 16 mislabelled duplicates)."""
    out: list[tuple[str, dict[str, float]]] = [("rest", {})]
    for j in robot.independent_joints():
        if j.type == "continuous":
            cands = [("half", math.pi / 2)]
        else:
            cands = [("lower", j.lower or 0.0), ("upper", j.upper or 0.0)]
        for tag, v in cands:
            if abs(v) > 1e-9:
                out.append((f"{j.name}@{tag}", {j.name: float(v)}))
    return out
