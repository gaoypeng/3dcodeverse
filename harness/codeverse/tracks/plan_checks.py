"""Plan-time geometry checks for articulated plans (2026-08-28).

The planner's boxes, pivots and axes are enough to catch the three mistakes that cost
compare_art_v3 its low scores — a pivot nowhere near the part it turns, a child that does
not touch the link it attaches to, and a part that sweeps through a neighbour over its
joint range — BEFORE any code is written.  Each mistake found here is a re-ask of the
planner (:data:`codeverse.tracks.planner.MAX_GEOMETRY_REASKS`), not a gate on a build
that already cost a round.

Everything is axis-aligned-box arithmetic on the plan's own numbers.  The checks are
deliberately conservative: the moving part's own subtree and any link whose box CONTAINS
>= ``HOUSING_FRACTION`` of the moving part at rest (a cabinet around a drawer, a frame around a sash) are never
collision candidates, and only a real intersection (>= ``COLLISION_MIN_M`` on every axis,
or half the part's thickness for thin parts) counts.
A false complaint costs one re-ask; a missed one costs nothing that the joint sweep does
not still catch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from codeverse.conventions import to_snake

#: a hinge line must lie within this margin of BOTH the child's and the parent's box
PIVOT_TOL_M = 0.025
#: a child's box must come within this gap of its parent's box (touching = 0)
ATTACH_GAP_M = 0.015
#: a swept box must overlap a neighbour by at least this on EVERY axis to count as a collision
COLLISION_MIN_M = 0.02
#: a link whose box holds at least this share of the moving part at rest is its housing
#: (cabinet around a drawer, frame around a sash) and is never a collision candidate
HOUSING_FRACTION = 0.8
#: points sampled per box axis when posing a part (5³ = 125 points; a point, unlike the
#: axis-aligned box of a rotated part, is never an over-approximation)
SAMPLES_PER_AXIS = 5
#: at most this many complaints per re-ask (the worst first)
MAX_COMPLAINTS = 6

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Box:
    lo: Vec
    hi: Vec

    @classmethod
    def from_bbox(cls, bbox: Any) -> Box:
        c, e = bbox.center, bbox.extents
        return cls(tuple(c[i] - e[i] / 2 for i in range(3)), tuple(c[i] + e[i] / 2 for i in range(3)))  # type: ignore[arg-type]

    @classmethod
    def of_points(cls, pts: list[Vec]) -> Box:
        return cls(tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))  # type: ignore[arg-type]

    def corners(self) -> list[Vec]:
        return [(x, y, z) for x in (self.lo[0], self.hi[0]) for y in (self.lo[1], self.hi[1]) for z in (self.lo[2], self.hi[2])]

    def samples(self, n: int = SAMPLES_PER_AXIS) -> list[Vec]:
        """An n×n×n lattice over the box (corners, edges, faces and interior)."""
        axes = [[self.lo[i] + (self.hi[i] - self.lo[i]) * k / (n - 1) for k in range(n)] for i in range(3)]
        return [(x, y, z) for x in axes[0] for y in axes[1] for z in axes[2]]

    def depth_of_point(self, p: Vec) -> float:
        """How far inside the box ``p`` lies (distance to the nearest face); <= 0 outside."""
        return min(min(p[i] - self.lo[i], self.hi[i] - p[i]) for i in range(3))

    def center(self) -> Vec:
        return tuple((self.lo[i] + self.hi[i]) / 2 for i in range(3))  # type: ignore[return-value]

    def gap_to(self, other: Box) -> float:
        """Largest per-axis separation (0 when the boxes touch or overlap)."""
        return max(0.0, *[max(self.lo[i] - other.hi[i], other.lo[i] - self.hi[i]) for i in range(3)])

    def dist_to_point(self, p: Vec) -> float:
        return math.sqrt(sum(max(self.lo[i] - p[i], 0.0, p[i] - self.hi[i]) ** 2 for i in range(3)))

    def overlap_depth(self, other: Box) -> float:
        """Smallest per-axis overlap (a true intersection depth); <= 0 when they do not intersect."""
        return min(min(self.hi[i], other.hi[i]) - max(self.lo[i], other.lo[i]) for i in range(3))

    def contains(self, other: Box, slack: float = 0.0) -> bool:
        return all(self.lo[i] - slack <= other.lo[i] and other.hi[i] <= self.hi[i] + slack for i in range(3))

    def volume(self) -> float:
        return math.prod(max(self.hi[i] - self.lo[i], 0.0) for i in range(3))

    def fraction_inside(self, other: Box) -> float:
        """Share of this box's volume that lies inside ``other`` (1 = fully housed).
        A degenerate (zero-volume) box falls back to containment with ``PIVOT_TOL_M`` slack."""
        v = self.volume()
        if v <= 0.0:
            return 1.0 if other.contains(self, slack=PIVOT_TOL_M) else 0.0
        inter = math.prod(max(min(self.hi[i], other.hi[i]) - max(self.lo[i], other.lo[i]), 0.0) for i in range(3))
        return inter / v


def _rotate(p: Vec, axis: Vec, pivot: Vec, angle: float) -> Vec:
    """Rodrigues rotation of ``p`` about the unit ``axis`` through ``pivot``."""
    ax, ay, az = axis
    n = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
    ax, ay, az = ax / n, ay / n, az / n
    vx, vy, vz = p[0] - pivot[0], p[1] - pivot[1], p[2] - pivot[2]
    c, s = math.cos(angle), math.sin(angle)
    dot = ax * vx + ay * vy + az * vz
    cx, cy, cz = ay * vz - az * vy, az * vx - ax * vz, ax * vy - ay * vx
    rx = vx * c + cx * s + ax * dot * (1 - c)
    ry = vy * c + cy * s + ay * dot * (1 - c)
    rz = vz * c + cz * s + az * dot * (1 - c)
    return (pivot[0] + rx, pivot[1] + ry, pivot[2] + rz)


def _posed_points(box: Box, joint: Any, q: float) -> list[Vec]:
    pts = box.samples()
    if joint.type in ("revolute", "continuous"):
        return [_rotate(c, tuple(joint.axis), tuple(joint.pivot), q) for c in pts]
    if joint.type == "prismatic":
        ax = tuple(joint.axis)
        n = math.sqrt(sum(a * a for a in ax)) or 1.0
        d = tuple(a / n * q for a in ax)
        return [(p[0] + d[0], p[1] + d[1], p[2] + d[2]) for p in pts]
    return pts


def _spins_in_place(box: Box, joint: Any) -> bool:
    """True when the joint axis passes through the part's core (a wheel on its axle, a lead
    screw, a mirror in a yoke): the part turns where it is and sweeps nothing new."""
    ax = tuple(joint.axis)
    n = math.sqrt(sum(a * a for a in ax)) or 1.0
    ax = (ax[0] / n, ax[1] / n, ax[2] / n)
    c, pv = box.center(), tuple(joint.pivot)
    v = (c[0] - pv[0], c[1] - pv[1], c[2] - pv[2])
    t = sum(v[i] * ax[i] for i in range(3))
    off = math.sqrt(max(sum((v[i] - t * ax[i]) ** 2 for i in range(3)), 0.0))
    return off <= 0.5 * min(box.hi[i] - box.lo[i] for i in range(3))


def _ancestors(link: str, parent_of: dict[str, str]) -> set[str]:
    out: set[str] = set()
    cur = parent_of.get(link)
    while cur is not None and cur not in out:
        out.add(cur)
        cur = parent_of.get(cur)
    return out


def geometry_complaints(plan: Any) -> list[str]:
    """Numbered, specific complaints about an :class:`ArticulatedPlan`'s geometry ([] = fine)."""
    parts = {to_snake(p.name): p for p in getattr(plan, "parts", [])}
    joints = list(getattr(plan, "joints", []) or [])
    if not parts or not joints:
        return []
    boxes = {k: Box.from_bbox(p.bbox) for k, p in parts.items()}
    parent_of = {to_snake(j.child): to_snake(j.parent) for j in joints}
    out: list[tuple[float, str]] = []  # (severity score, text)

    for j in joints:
        c, p = to_snake(j.child), to_snake(j.parent)
        if c not in boxes or p not in boxes:
            continue
        cb, pb = boxes[c], boxes[p]
        # A. attachment: the child's box must reach its parent's box
        gap = cb.gap_to(pb)
        if gap > ATTACH_GAP_M:
            out.append((gap, f"joint {j.name}: link {j.child} is {gap * 1000:.0f} mm away from its parent {j.parent} "
                             f"(boxes do not touch) — move or enlarge {j.child} so it meets {j.parent}, or attach it "
                             f"to the link it really sits on"))
        if j.type == "fixed":
            continue
        # B. hinge placement: the pivot of a revolute / continuous joint lies where the two
        #    links meet.  (A prismatic pivot is only the child frame's origin — anywhere on
        #    the rail is legal — so it is not checked.)
        pv = tuple(j.pivot)
        if j.type in ("revolute", "continuous"):
            dc, dp = cb.dist_to_point(pv), pb.dist_to_point(pv)
            if dc > PIVOT_TOL_M or dp > PIVOT_TOL_M:
                far = j.child if dc > dp else j.parent
                out.append((max(dc, dp), f"joint {j.name}: pivot {[round(x, 3) for x in pv]} is {max(dc, dp) * 1000:.0f} mm "
                                          f"from {far}'s box (hinge lines sit where the two links meet) — "
                                          f"put the pivot on the shared edge of {j.parent} and {j.child}"))
            if _spins_in_place(cb, j):
                continue  # turns where it is: nothing to sweep
        # C. swept collision.  The child's box is posed at q=lower/upper as a point lattice
        #    and tested against every link that is neither carried by the child (its
        #    subtree), a housing round it (>= HOUSING_FRACTION of it at rest), interlocked
        #    with it at rest (they already intersect — crossing scissor arms, folded ribs,
        #    a screw through a jaw: the joint sweep measures the real meshes later), nor —
        #    for a slide — the parent it runs in/on.  The parent of a hinge IS a candidate
        #    when it does not house the child: a door must not swing through its cabinet.
        #    Thin parts count at half their thickness.
        qs = [j.lower, j.upper] if j.type in ("revolute", "prismatic") else [math.pi / 2, math.pi]
        exclude = {c} | {k for k in boxes if c in _ancestors(k, parent_of)}
        if j.type == "prismatic":
            exclude.add(p)
        min_depth = max(0.001, min(COLLISION_MIN_M, 0.5 * min(cb.hi[i] - cb.lo[i] for i in range(3))))
        for other, ob in boxes.items():
            if other in exclude or cb.fraction_inside(ob) >= HOUSING_FRACTION or cb.overlap_depth(ob) >= min_depth:
                continue
            worst_q, worst_depth = None, 0.0
            for q in qs:
                if abs(q) < 1e-9:
                    continue
                depth = max(ob.depth_of_point(pt) for pt in _posed_points(cb, j, q))
                if depth > worst_depth:
                    worst_q, worst_depth = q, depth
            if worst_q is not None and worst_depth >= min_depth:
                unit = "rad" if j.type != "prismatic" else "m"
                out.append((worst_depth, f"joint {j.name}: at q={worst_q:.2f} {unit} link {j.child} sweeps "
                                          f"{worst_depth * 1000:.0f} mm deep into {parts[other].name} — shorten "
                                          f"{j.child} on that side, move the pivot, or reduce the range "
                                          f"[{j.lower}, {j.upper}] so the motion clears {parts[other].name}"))
    out.sort(key=lambda t: -t[0])
    return [t for _, t in out[:MAX_COMPLAINTS]]


def plan_geometry_complaint(plan: Any) -> str:
    """"" when the plan's geometry is consistent; otherwise the re-ask text."""
    items = geometry_complaints(plan)
    if not items:
        return ""
    return ("Your plan is valid but its geometry contradicts itself. Fix EXACTLY these problems (move boxes, pivots "
            "or ranges; keep every part and joint) and return the full corrected plan JSON again (same schema):\n- "
            + "\n- ".join(items))


__all__ = ["ATTACH_GAP_M", "COLLISION_MIN_M", "HOUSING_FRACTION", "MAX_COMPLAINTS", "PIVOT_TOL_M", "SAMPLES_PER_AXIS", "Box", "geometry_complaints",
           "plan_geometry_complaint"]
