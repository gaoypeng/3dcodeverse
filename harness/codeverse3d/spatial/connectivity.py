"""Connectivity gate: are all parts of the GLB physically attached?

Mechanism
---------
1. Load one world-space mesh per top-level node (``measure.solid_parts``,
   memoized on the file's stat so repeat gates on one GLB parse it once).
2. For every part pair whose AABBs come within ``gap_m`` of each other compute
   the exact minimum surface distance (``fcl.distance`` on per-part BVHs that
   are built once and shared across all pairs; sampled closest-point fallback
   when fcl is missing) and the closest points.
3. Contact graph: edge when distance ≤ ``gap_m``.  The *support component* is
   the connected component holding the ground-touching parts (lowest point at
   y ≤ gap) — or the largest component when nothing touches the ground.
4. Parts outside the support component are **floating** → ERROR with the
   nearest supported part and the exact translation vector that closes the gap.
5. Overlapping AABB pairs are probed for mutual containment of surface samples
   (:func:`penetration_probe`): the whole surface first, then densely where the
   overlap is, so the tip of a thin member inside another part is measured with
   enough points.  A pair is reported when the depth is above
   ``PENETRATION_WARN_M`` and the overlap is either a visible share of the whole
   part (``PENETRATION_MIN_FRACTION``) or dense where it is
   (``PENETRATION_MIN_INSIDE`` / ``PENETRATION_MIN_LOCAL_FRACTION``); ERROR
   above ``PENETRATION_ERROR_M`` when the overlap is also a visible share of the
   part, else WARN (parts are allowed to overlap by a hair for welding, not to
   pass through each other; a deep overlap only the dense pass can see is
   reported in full and left to the judge).  The through-ratio — how far
   the entering member reaches into the part it enters — is measured and named in
   the message, never a severity (``THROUGH_FAR_SIDE_RATIO``).
6. Tiny disconnected islands inside a single part → WARN (stray geometry).
7. One INFO *contact ledger*: every contact with its gap, every measured overlap
   down to the sub-threshold welds, the ground gap per part and the plan's
   ``attach_to`` edges measured regardless of the AABB prefilter
   (``planned_edges``).  INFO never changes ``passed``; the judge payload reads it.

All thresholds are module constants (meters) and keyword-overridable.  Pass the
authoring ``language`` so translation hints are written in the frame the agent
codes in (Blender/CadQuery/URDF are Z-up; the GLB is Y-up) — a hint in the wrong
frame moves a part forward instead of down.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.conventions import CONTACT_GAP_M, Frame
from codeverse3d.spatial import joints_collide as collide
from codeverse3d.spatial.contract import _aabb_gap, frame_label, glb_vec_to_plan, language_frame
from codeverse3d.spatial.joints_collide import fcl_collision_object, inside_island, oriented_islands
from codeverse3d.spatial.measure import GlbLoadError, fmt_vec, solid_parts

GATE = "connectivity"
#: one planned join: (child, parent) or (child, (parent copies the child's box touches, …))
PlannedEdge = tuple[str, "str | Sequence[str]"]
#: parts smaller than this (max extent) are ignored for floating checks (INFO only)
MIN_PART_SIZE_M = 0.01
#: penetration depth that is reported as WARN / ERROR.  These bound the REST pose only,
#: and with a 600-point surface draw per part: this check asks "do these two surfaces sit
#: inside each other as built".  ``joint_sweep`` asks a different question on posed meshes
#: — "does moving a joint drive one link through another" — at its own thresholds.  They
#: are not one check with two dials: do not align the numbers (DECISIONS.md D53, with the
#: corpus measurement behind it in eval/bench/penetration_thresholds.py).
PENETRATION_WARN_M = 0.002
PENETRATION_ERROR_M = 0.010
#: fraction of one part's samples inside the other before we call it penetration ...
PENETRATION_MIN_FRACTION = 0.02
#: ... or, when the overlap is a small share of a large part, dense where it is: at
#: least this many samples inside and this share of the samples in the overlap region.
#: Measured 2026-08-30 on h2h_telescope: the spreader arm tips 4 mm inside three 24 mm
#: legs were 4–6 of 600 whole-surface samples (0.7–1.0 %), under the fraction floor,
#: while a third of the arm's surface at the joint was inside the leg.
PENETRATION_MIN_INSIDE = 4
PENETRATION_MIN_LOCAL_FRACTION = 0.2
#: through_ratio = 2 · depth / thickness of the part entered: 1 means the entering
#: member reaches the far side (the rubric's "rail poking out of the far side of a leg").
#: It is MEASURED AND REPORTED, never a severity of its own: the corpus re-run
#: (2026-08-30, 217 runs) showed a 0.9 ERROR line firing on 31 pairs in 9 runs that are
#: designed joinery — a boom seated 5 mm into a mast (0.96), arch stretchers 8 mm into
#: 8 mm ribs (0.91–0.97) — because the number saturates at the container's mid-plane and
#: cannot tell "ends inside" from "out the far side".  Severity stays on depth alone
#: (the contract's own 2 mm / 10 mm lines); the judge, who sees the picture, gets the
#: ratio as a fact in the message and the ledger.
THROUGH_FAR_SIDE_RATIO = 0.9
#: surface samples per part for containment / fallback distance tests
N_SAMPLES = 600
#: when fewer than LOCAL_MIN_SAMPLES of the entering part's whole-surface samples lie in
#: the overlap region, LOCAL_EXTRA_SAMPLES more are drawn on its faces CLIPPED to the region
#: (a plate's 300 mm face around an 8 mm rod is 0.05 % in-region; rejection sampling it
#: never reached the rod's axis).  The max depth of a finite sample under-estimates the
#: true reach by ~ radius/√n, which is why 512 and not 64: at 64, a 24 mm rail through a
#: 10 mm plate read 0.89, one hair under THROUGH_FAR_SIDE_RATIO
LOCAL_MIN_SAMPLES = 128
LOCAL_EXTRA_SAMPLES = 512
#: the entering part's vertices inside the AABB overlap are tested too: a tip's end cap
#: is a vertex ring, and vertices find an overlap smaller than 1/N_SAMPLES of the
#: surface that uniform samples miss (capped so a 200k-vertex part is one ray pass)
LOCAL_MAX_VERTICES = 1500
_REGION_HAIR_M = 0.001
#: an island is "tiny" when its largest extent is below this fraction of the part's
TINY_ISLAND_FRACTION = 0.05
_RNG_SEED = 7


@dataclass(frozen=True)
class PairDistance:
    a: str
    b: str
    distance: float
    point_a: tuple[float, float, float]
    point_b: tuple[float, float, float]

    @property
    def gap_vector(self) -> tuple[float, float, float]:
        """Translation to apply to ``a`` so that it touches ``b``."""
        return tuple(float(pb - pa) for pa, pb in zip(self.point_a, self.point_b, strict=True))  # type: ignore[return-value]


@dataclass(frozen=True)
class Penetration:
    """The deepest direction of an overlap probe: ``entering``'s surface inside ``container``.

    ``fraction_inside`` is the share of N_SAMPLES whole-surface samples inside the other
    part, max over both directions — the pre-2026-08-30 number, kept for comparability
    across the corpus.  ``local_fraction`` / ``inside_count`` are measured on the samples
    that lie in the overlap region only, which is what makes a 10 mm tip on a 400 mm arm
    visible; ``thickness_m`` is the container's (:func:`part_thickness`).

    ``through_ratio`` is the PAIR's: the smaller of the two directions' 2 · depth /
    thickness.  One direction alone reads a 5 mm disc sunk 2.3 mm into a base as "the base
    reaches 82 % of the way through the disc" (h2h_microscope FieldIlluminator) — a designed
    inset.  A member that really passes through another scores ≈ 1 both ways: the rod
    reaches the plate's mid-plane AND the plate's faces cut through the rod's whole
    cross-section; an inset or socket is high one way and low the other."""

    entering: str
    container: str
    depth_m: float = 0.0
    fraction_inside: float = 0.0
    local_fraction: float = 0.0
    inside_count: int = 0
    thickness_m: float = 0.0
    through_ratio: float = 0.0

    @property
    def own_through_ratio(self) -> float:
        """This direction's 2 · depth / thickness of the container, clipped to [0, 2]: the
        deepest a surface point can sit inside a member is half its thickness."""
        if self.thickness_m <= 0.0:
            return 0.0
        return float(min(2.0, max(0.0, 2.0 * self.depth_m / self.thickness_m)))

    def as_data(self) -> dict[str, Any]:
        return {"entering": self.entering, "container": self.container, "depth_m": self.depth_m,
                "fraction_inside": self.fraction_inside, "local_fraction": self.local_fraction,
                "inside_count": self.inside_count, "through_ratio": self.through_ratio, "thickness_m": self.thickness_m}


class _PartCache:
    """Per-part collision geometry built once and shared across every pair: the
    ``fcl.CollisionObject`` (None when fcl is missing), the oriented islands the
    containment test runs on, and the thickness.  Before 2026-08-30 the islands were
    re-split per pair — on a 20-part model that is ~190 splits of the same 20 meshes."""

    def __init__(self, parts: dict[str, trimesh.Trimesh]):
        self._parts = parts
        self._fcl: dict[str, Any] = {}
        self._islands: dict[str, list[trimesh.Trimesh]] = {}
        self._thickness: dict[str, float] = {}

    def fcl(self, name: str) -> Any:
        if name not in self._fcl:
            self._fcl[name] = fcl_collision_object(self._parts[name])
        return self._fcl[name]

    def islands(self, name: str) -> list[trimesh.Trimesh]:
        if name not in self._islands:
            self._islands[name] = oriented_islands(self._parts[name])
        return self._islands[name]

    def thickness(self, name: str) -> float:
        if name not in self._thickness:
            self._thickness[name] = part_thickness(self._parts[name])
        return self._thickness[name]


def pair_distance(
    name_a: str,
    a: trimesh.Trimesh,
    name_b: str,
    b: trimesh.Trimesh,
    *,
    obj_a: Any = None,
    obj_b: Any = None,
) -> PairDistance:
    """Exact (fcl) or sampled minimum distance between two meshes with closest points.

    ``obj_a``/``obj_b`` accept prebuilt ``fcl.CollisionObject``\\ s (see
    :func:`codeverse3d.spatial.joints_collide.fcl_collision_object`) so a caller
    testing many pairs builds each part's BVH once instead of once per pair.
    """
    if collide._fcl is not None:
        try:
            oa = obj_a if obj_a is not None else fcl_collision_object(a)
            ob = obj_b if obj_b is not None else fcl_collision_object(b)
            req = collide._fcl.DistanceRequest(enable_nearest_points=True)
            res = collide._fcl.DistanceResult()
            d = collide._fcl.distance(oa, ob, req, res)
            pa, pb = res.nearest_points
            return PairDistance(name_a, name_b, float(max(d, 0.0)), tuple(map(float, pa)), tuple(map(float, pb)))
        except Exception:
            pass  # fall through to the sampled estimate
    rng = np.random.default_rng(_RNG_SEED)
    best: PairDistance | None = None
    for src, dst, flip in ((a, b, False), (b, a, True)):
        pts = np.vstack([src.vertices, _sample(src, N_SAMPLES, rng)])
        closest, dist, _ = trimesh.proximity.closest_point(dst, pts)
        i = int(np.argmin(dist))
        p_src, p_dst = tuple(map(float, pts[i])), tuple(map(float, closest[i]))
        cand = PairDistance(name_a, name_b, float(dist[i]), *((p_dst, p_src) if flip else (p_src, p_dst)))
        if best is None or cand.distance < best.distance:
            best = cand
    assert best is not None
    return best


def _sample(mesh: trimesh.Trimesh, n: int, rng: np.random.Generator) -> np.ndarray:
    """Deterministic surface samples (seeded)."""
    pts, _ = trimesh.sample.sample_surface(mesh, n, seed=int(rng.integers(1 << 30)))
    return np.asarray(pts, dtype=float)


def part_thickness(mesh: trimesh.Trimesh) -> float:
    """Thinnest extent of ``mesh``: the smaller of its AABB and its PCA-aligned box —
    a tube's diameter or a plate's thickness whichever way it is turned.  The AABB
    alone reads a diagonal 24 mm tripod leg as 311 mm (h2h_telescope TripodLegs_1)."""
    v = np.asarray(mesh.vertices, dtype=float)
    ext = float(np.min(mesh.extents))
    if len(v) < 4:
        return ext
    c = v - v.mean(axis=0)
    _, axes = np.linalg.eigh(c.T @ c)
    p = c @ axes
    return float(min(ext, np.min(p.max(axis=0) - p.min(axis=0))))


def _inside_depth(islands: list[trimesh.Trimesh], pts: np.ndarray) -> tuple[np.ndarray, float]:
    """(mask of ``pts`` inside one of ``islands``, max distance of those to the surface).

    Containment is the per-island, fixed-direction parity ray test the articulation gate
    already uses (:func:`codeverse3d.spatial.joints_collide.inside_island`), NOT
    ``Trimesh.contains``.  ``contains`` needs a watertight mesh, so this used to
    ``continue`` past any open shell — and when NEITHER part was watertight it returned
    (0.0, 0.0), indistinguishable from "no overlap", with no warning that the check had
    not run.  A 50 mm post driven into a base passed the gate as soon as both parts were
    modelled as open-bottomed shells, which is the common case for agent-exported meshes
    (open shells, boolean leftovers, mirrored halves, single-sided planes); only the
    mixed closed/open case still fired, which is why the shipped tests stayed green.
    Islands that are not watertight are tested the same way and the answer is
    approximate — the same trade joints_collide.penetration documents and flags."""
    inside = np.zeros(len(pts), dtype=bool)
    depth = 0.0
    if len(pts) == 0:
        return inside, depth
    for island in islands:
        try:
            hit = inside_island(island, pts)
        except Exception:  # noqa: BLE001 - a degenerate island must not fail the gate
            continue
        if not hit.any():
            continue
        _, dist, _ = trimesh.proximity.closest_point(island, pts[hit])
        depth = max(depth, float(np.max(dist)))
        inside |= hit
    return inside, depth


def _in_box(pts: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.all((pts >= lo) & (pts <= hi), axis=1)


def _faces_touching(mesh: trimesh.Trimesh, lo: np.ndarray, hi: np.ndarray) -> trimesh.Trimesh | None:
    """The sub-mesh of faces whose bounds meet the box — what the region resample draws on."""
    tri = mesh.triangles
    mask = np.all(tri.max(axis=1) >= lo, axis=1) & np.all(tri.min(axis=1) <= hi, axis=1)
    if not mask.any():
        return None
    sub = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces[mask], process=False)
    return sub if float(sub.area) > 0.0 else None


def _region_samples(src: trimesh.Trimesh, lo: np.ndarray, hi: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """LOCAL_EXTRA_SAMPLES surface samples of ``src`` inside the box: its touching faces are
    clipped by the box's six planes and sampled (rejection sampling of the unclipped faces
    is the fallback when the clip fails or leaves nothing)."""
    sub = _faces_touching(src, lo, hi)
    if sub is None:
        return np.zeros((0, 3), dtype=float)
    try:
        clipped = trimesh.intersections.slice_mesh_plane(
            sub, plane_normal=np.vstack([np.eye(3), -np.eye(3)]), plane_origin=np.vstack([[lo] * 3, [hi] * 3]), cap=False)
        if isinstance(clipped, trimesh.Trimesh) and len(clipped.faces) and float(clipped.area) > 0.0:
            sub = clipped
    except Exception:  # noqa: BLE001 - a degenerate sub-mesh falls back to rejection sampling
        pass
    extra = _sample(sub, LOCAL_EXTRA_SAMPLES, rng)
    return extra[_in_box(extra, lo, hi)]


def _probe_direction(
    name_src: str,
    src: trimesh.Trimesh,
    name_dst: str,
    dst: trimesh.Trimesh,
    cache: _PartCache,
    pts: np.ndarray,
    seeds: np.ndarray | None,
    rng: np.random.Generator,
) -> Penetration:
    """``src``'s surface inside ``dst``: the whole-surface samples ``pts`` first, then a
    dense pass on the region where the overlap actually is (around the inside samples,
    the inside vertices and ``seeds``, grown by half the thinner part) — or on the whole
    AABB overlap when it is too small to have caught LOCAL_MIN_SAMPLES samples."""
    islands = cache.islands(name_dst)
    lo = np.maximum(src.bounds[0], dst.bounds[0]) - _REGION_HAIR_M
    hi = np.minimum(src.bounds[1], dst.bounds[1]) + _REGION_HAIR_M
    hit, depth = _inside_depth(islands, pts)
    frac = float(np.mean(hit)) if len(hit) else 0.0
    verts = np.asarray(src.vertices, dtype=float)
    verts = verts[_in_box(verts, lo, hi)]
    if len(verts) > LOCAL_MAX_VERTICES:
        verts = verts[np.linspace(0, len(verts) - 1, LOCAL_MAX_VERTICES).astype(int)]
    hit_v, depth_v = _inside_depth(islands, verts)
    depth = max(depth, depth_v)
    # fcl's closest points of two intersecting Convex objects are (0, 0, 0): keep only seeds
    # that lie in the AABB overlap
    seeds = seeds[_in_box(seeds, lo, hi)] if seeds is not None and len(seeds) else np.zeros((0, 3))
    found = np.vstack([pts[hit], verts[hit_v], seeds])
    in_box = _in_box(pts, lo, hi)
    thickness = cache.thickness(name_dst)
    if len(found) == 0:
        if int(in_box.sum()) >= LOCAL_MIN_SAMPLES:
            return Penetration(name_src, name_dst, depth, frac, 0.0, 0, thickness)
        region_lo, region_hi = lo, hi
    else:
        margin = max(PENETRATION_WARN_M, 0.5 * min(cache.thickness(name_src), thickness))
        region_lo = np.minimum(np.maximum(lo, found.min(axis=0) - margin), hi)
        region_hi = np.maximum(np.minimum(hi, found.max(axis=0) + margin), region_lo)
    in_region = _in_box(pts, region_lo, region_hi)
    inside, n_local = int(hit[in_region].sum()), int(in_region.sum())
    if n_local < LOCAL_MIN_SAMPLES:
        extra = _region_samples(src, region_lo, region_hi, rng)
        hit_e, depth_e = _inside_depth(islands, extra)
        depth = max(depth, depth_e)
        inside += int(hit_e.sum())
        n_local += len(extra)
    return Penetration(name_src, name_dst, depth, frac, inside / n_local if n_local else 0.0, inside, thickness)


def penetration_probe(
    name_a: str,
    a: trimesh.Trimesh,
    name_b: str,
    b: trimesh.Trimesh,
    *,
    cache: _PartCache | None = None,
    seeds: np.ndarray | None = None,
) -> Penetration:
    """Mutual containment of ``a`` and ``b``, both directions; the deeper one is returned
    with ``fraction_inside`` = the max over both.  ``seeds`` are known overlap points (the
    fcl closest points of a pair at distance 0) that localise the dense pass."""
    cache = cache if cache is not None else _PartCache({name_a: a, name_b: b})
    # the two whole-surface draws come first, in the pre-2026-08-30 order, so fraction_inside
    # is the same number the corpus was scored with; the dense pass draws from its own stream
    rng = np.random.default_rng(_RNG_SEED)
    pts_a, pts_b = _sample(a, N_SAMPLES, rng), _sample(b, N_SAMPLES, rng)
    ab = _probe_direction(name_a, a, name_b, b, cache, pts_a, seeds, rng)
    ba = _probe_direction(name_b, b, name_a, a, cache, pts_b, seeds, rng)
    worst = max(ab, ba, key=lambda p: (p.depth_m, p.inside_count))
    return Penetration(worst.entering, worst.container, worst.depth_m, max(ab.fraction_inside, ba.fraction_inside),
                       worst.local_fraction, worst.inside_count, worst.thickness_m,
                       min(ab.own_through_ratio, ba.own_through_ratio))


def penetration_depth(a: trimesh.Trimesh, b: trimesh.Trimesh) -> tuple[float, float]:
    """(max depth, fraction of samples inside) of ``a``'s surface inside ``b`` and vice versa."""
    p = penetration_probe("a", a, "b", b)
    return p.depth_m, p.fraction_inside


def _fmt_vec(v: tuple[float, float, float]) -> str:
    """Millimetre-precision translation vector (shared formatter, 4 decimals)."""
    return fmt_vec(v, 4)


def _mm(x: float) -> float:
    return round(float(x) * 1000.0, 1)


def _island_findings(name: str, mesh: trimesh.Trimesh, islands: list[trimesh.Trimesh]) -> list[GateFinding]:
    if len(islands) <= 1:
        return []
    ext = float(np.max(mesh.extents))
    tiny = [i for i in islands if float(np.max(i.extents)) < TINY_ISLAND_FRACTION * ext]
    out = []
    if tiny:
        out.append(GateFinding(
            gate=GATE, severity=Severity.WARN, target=name,
            message=f"part '{name}' contains {len(tiny)} tiny disconnected island(s) (< {TINY_ISLAND_FRACTION:.0%} of the part size) — stray geometry",
            fix_hint=f"remove the stray pieces or merge them into '{name}'; the part should be {len(islands) - len(tiny)} solid piece(s)",
            data={"islands": len(islands), "tiny_islands": len(tiny)},
        ))
    return out


def _penetration_finding(a: str, b: str, pen: Penetration, warn_m: float, error_m: float) -> GateFinding:
    through = pen.through_ratio
    # ERROR keeps the pre-2026-08-30 meaning — deep AND a visible share of the part
    # (``PENETRATION_MIN_FRACTION``) — now with the depth measured properly.  A pair found
    # only by the dense local pass (a stile 17 mm through a seat slab, 1 % of either surface)
    # is reported with every number but stays a WARN: with ERROR by depth alone the corpus
    # re-run flipped 27 runs of such joinery pass→fail on a cap the eye cannot confirm; the
    # judge, who sees the picture, is the one who can (D46 d).  What still flips (14 of 217
    # runs) is a pair the old gate already reported, now measured 9.4 → 11.3 mm deep.
    sev = (Severity.ERROR if pen.depth_m > error_m and pen.fraction_inside >= PENETRATION_MIN_FRACTION
           else Severity.WARN)
    msg = (f"'{a}' and '{b}' interpenetrate by ≈{pen.depth_m * 1000:.1f} mm "
           f"({pen.fraction_inside:.0%} of surface samples inside, {pen.local_fraction:.0%} where they meet)")
    if through >= 0.5:
        # the ratio saturates at the mid-plane: a tenon that STOPS there and a rail that passes
        # clean through read the same, so the words say what was measured and no more
        msg += (f" — '{pen.entering}' reaches {min(through, 1.0):.0%} of the way to the mid-plane of "
                f"'{pen.container}' ({pen.thickness_m * 1000:.0f} mm thick)")
    if through >= THROUGH_FAR_SIDE_RATIO:
        hint = (f"'{pen.entering}' reaches the middle of '{pen.container}' — as far in as a surface can be "
                f"measured: if it is meant to stop inside (a tenon), fine; if it should end at the surface, "
                f"shorten it; if it is a continuous member (a stile through a seat), notch '{pen.container}' "
                f"around it or accept the overlap (a hairline overlap is fine for welding)")
    else:
        hint = f"shrink or move '{a}'/'{b}' so they overlap by ≤ {warn_m * 1000:.0f} mm (a hairline overlap is fine for welding)"
    return GateFinding(gate=GATE, severity=sev, target=a, message=msg, fix_hint=hint,
                       data={"kind": "penetration", "other": b, **pen.as_data()})


def _ledger(
    names: list[str],
    parts: dict[str, trimesh.Trimesh],
    edges: set[tuple[str, str]],
    exact: dict[tuple[str, str], PairDistance],
    overlaps: list[tuple[str, str, Penetration]],
    planned: list[list[Any]],
    unresolved: list[str],
    gap_m: float,
) -> GateFinding:
    """The per-pair table ``check_connectivity`` used to compute and drop at exit: what
    the judge payload needs to say "these are welds" without the VLM guessing."""
    order = {n: i for i, n in enumerate(names)}
    contacts = [[a, b, _mm(exact[(a, b)].distance)] for a, b in sorted(edges, key=lambda e: (order[e[0]], order[e[1]]))]
    over = [[a, b, _mm(p.depth_m), round(p.through_ratio, 2)] for a, b, p in overlaps if _mm(p.depth_m) > 0.0]
    return GateFinding(
        gate=GATE, severity=Severity.INFO, target="",
        message=f"contact ledger: {len(names)} parts, {len(contacts)} contacts, {len(over)} overlaps",
        data={"kind": "ledger", "parts": list(names), "contact_gap_mm": _mm(gap_m), "contacts": contacts, "overlaps": over,
              "ground_gap_mm": {n: _mm(float(parts[n].bounds[0][1])) for n in names},
              "planned": planned, "planned_unresolved": unresolved},
    )


def check_connectivity(
    glb: Path | str,
    *,
    gap_m: float = CONTACT_GAP_M,
    min_part_size_m: float = MIN_PART_SIZE_M,
    penetration_warn_m: float = PENETRATION_WARN_M,
    penetration_error_m: float = PENETRATION_ERROR_M,
    language: str = "",
    planned_edges: Sequence[PlannedEdge] = (),
) -> GateReport:
    """Run the connectivity gate on ``glb`` (see module docstring).  ``language`` selects
    the frame of the translation hints (default: the GLB frame, labelled as such).
    ``planned_edges`` are the plan's ``attach_to`` pairs: each is measured exactly and
    listed in the ledger as contact/open; an open one is reported, not an ERROR — whether
    a missed planned join fails the gate is the owner's call (not made, 2026-08-30)."""
    t0 = time.time()
    up = "z" if language_frame(language) is Frame.Z_UP_NEG_Y_FRONT else "y"
    findings: list[GateFinding] = []
    try:
        parts = solid_parts(glb)
    except GlbLoadError as e:
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, message=str(e), fix_hint="run `build` first"))
        return GateReport(gate=GATE, passed=False, findings=findings, duration_ms=int((time.time() - t0) * 1000))
    if not parts:
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, message="GLB has no mesh parts",
                                    fix_hint="the build exported nothing — check the code creates geometry"))
        return GateReport(gate=GATE, passed=False, findings=findings, duration_ms=int((time.time() - t0) * 1000))

    names = list(parts)
    sizes = {n: float(np.max(parts[n].extents)) for n in names}
    big = [n for n in names if sizes[n] >= min_part_size_m]
    for n in names:
        if n not in big:
            findings.append(GateFinding(gate=GATE, severity=Severity.INFO, target=n,
                                        message=f"part '{n}' is tiny ({sizes[n] * 1000:.1f} mm) — skipped for contact checks"))
    # ---- pairwise distances (AABB prefilter) + penetration
    edges: set[tuple[str, str]] = set()
    exact: dict[tuple[str, str], PairDistance] = {}
    overlaps: list[tuple[str, str, Penetration]] = []
    cache = _PartCache(parts)  # one BVH / island split / thickness per part, shared across pairs
    for a, b in itertools.combinations(big, 2):
        lower = _aabb_gap(parts[a].bounds, parts[b].bounds)
        if lower > gap_m:
            continue
        pd = pair_distance(a, parts[a], b, parts[b], obj_a=cache.fcl(a), obj_b=cache.fcl(b))
        exact[(a, b)] = pd
        if pd.distance <= gap_m:
            edges.add((a, b))
        if lower == 0.0:  # AABBs overlap → possible penetration
            # at distance 0 fcl's closest points lie ON the overlap: a seed for the dense pass
            seeds = np.array([pd.point_a, pd.point_b], dtype=float) if pd.distance <= 0.0 else None
            if seeds is not None:
                # two intersecting Convex objects give fcl nothing to report and it returns the
                # ORIGIN as both closest points; a part centred on the origin would keep it as
                # a seed and widen the dense region over nothing
                seeds = seeds[np.any(seeds != 0.0, axis=1)]
                seeds = seeds if len(seeds) else None
            pen = penetration_probe(a, parts[a], b, parts[b], cache=cache, seeds=seeds)
            if pen.depth_m > 0.0:
                overlaps.append((a, b, pen))
            dense = pen.inside_count >= PENETRATION_MIN_INSIDE and pen.local_fraction >= PENETRATION_MIN_LOCAL_FRACTION
            if pen.depth_m > penetration_warn_m and (pen.fraction_inside >= PENETRATION_MIN_FRACTION or dense):
                findings.append(_penetration_finding(a, b, pen, penetration_warn_m, penetration_error_m))
    # ---- components → floating parts
    comps = collide.components(big, edges)
    # TOUCHING the ground, not merely at-or-below it: the old one-sided `<= gap_m`
    # counted a part buried 1 m under the floor as grounded, so a model authored around
    # the origin instead of on it (a routine agent mistake, which the contract gate
    # already reports separately as "object floats 1000 mm below the ground") put every
    # part in `grounded`, hence in `support`, and switched the floating check off for the
    # whole run.  abs() makes the sunk model fall through to the "no part touches the
    # ground" WARN branch and report the same floating ERROR it does at y=0.
    grounded = {n for n in big if abs(float(parts[n].bounds[0][1])) <= gap_m}
    support_sets = [c for c in comps if c & grounded]
    if support_sets:
        # THE support component (see the module docstring): ONE component, not the union
        # of every ground-touching one.  Unioning made this max() dead and let any part
        # that merely reaches y=0 count as supported, so the commonest static-object
        # defect -- a leg 5 mm short of the seat, a rail shy of its post -- passed as long
        # as the part still stood on the floor, and the INFO line then claimed "all 5
        # parts are connected (3 contacts)" for a graph that needs >= 4 edges.  It also
        # mutated the winning set in place, corrupting `comps`; hence the copy.
        support = set(max(support_sets, key=lambda c: sum(len(parts[n].faces) for n in c)))
    else:
        # Nothing touches the ground, so there is no ground to reason from: take the
        # LOWEST component as the base (face count only breaks ties).  Picking the
        # biggest instead made a model authored around the origin report its base as the
        # floating part and the thing sitting on top as the support — the same finding as
        # the correctly-placed model, but with the two parts swapped.
        support = min(comps, key=lambda c: (round(min(float(parts[n].bounds[0][1]) for n in c), 6),
                                            -sum(len(parts[n].faces) for n in c))) if comps else set()
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, message=f"no part touches the ground ({up}=0)",
                                    fix_hint=f"move the whole object down so its lowest point sits on the ground ({up}=0, {frame_label(language)})"))
    for n in big:
        if n in support:
            continue
        near = _nearest_supported(n, parts, support, exact, cache)
        if near is None:
            findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=n, data={"kind": "floating"},
                                        message=f"part '{n}' is floating: touches nothing", fix_hint=f"attach '{n}' to a neighbouring part"))
            continue
        other = near.b if near.a == n else near.a
        vec_glb = near.gap_vector if near.a == n else tuple(-x for x in near.gap_vector)
        p_self = near.point_a if near.a == n else near.point_b
        p_other = near.point_b if near.a == n else near.point_a
        vec = tuple(float(x) for x in glb_vec_to_plan(vec_glb, language))
        findings.append(GateFinding(
            gate=GATE, severity=Severity.ERROR, target=n,
            message=f"part '{n}' is floating: nearest supported part is '{other}' at {near.distance * 1000:.1f} mm",
            fix_hint=f"translate '{n}' by {_fmt_vec(vec)} m ({frame_label(language)}) — or extend it by "
                     f"{near.distance * 1000:.1f} mm towards '{other}' — so the surfaces touch",
            data={"kind": "floating", "nearest": other, "gap_m": near.distance, "gap_vector_m": list(vec),
                  "frame": language_frame(language).value, "gap_vector_glb_m": list(vec_glb),
                  "closest_point_self": list(p_self), "closest_point_other": list(p_other)},
        ))
    # ---- stray islands inside parts
    for n in big:
        findings.extend(_island_findings(n, parts[n], cache.islands(n)))
    # ---- the plan's attach_to edges, measured whatever the AABB prefilter said
    planned: list[list[Any]] = []
    unresolved: list[str] = []
    seen_pairs: set[frozenset[str]] = set()
    for a, cands in planned_edges:
        names_b = (cands,) if isinstance(cands, str) else tuple(cands)
        if a not in parts:
            if a not in unresolved:
                unresolved.append(a)
            continue
        rows: list[list[Any]] = []
        for b in names_b:
            if b not in parts:
                if b not in unresolved:
                    unresolved.append(b)
                continue
            if a == b or frozenset((a, b)) in seen_pairs:
                continue
            seen_pairs.add(frozenset((a, b)))
            pd = exact.get((a, b)) or exact.get((b, a))
            if pd is None:
                pd = pair_distance(a, parts[a], b, parts[b], obj_a=cache.fcl(a), obj_b=cache.fcl(b))
                exact[(a, b)] = pd
            rows.append([a, b, _mm(pd.distance), "contact" if pd.distance <= gap_m else "open"])
        # a group is ONE planned join offered against every parent copy the child's box
        # touches (contract.planned_joins): the exact distances say which copy it meant —
        # every CONTACT row, else the nearest OPEN one.  Resolving the tie by list order
        # printed "OPEN: Brace_1→Leg_0" for a join the plan never meant (skeptic 2026-08-30);
        # reducing per CHILD instead hid a second, genuinely open join.
        contacts = [r for r in rows if r[3] == "contact"]
        planned.extend(contacts if contacts else rows[:1] if len(rows) <= 1 else [min(rows, key=lambda r: r[2])])
    if len(big) > 1 and not any(f.severity == Severity.ERROR for f in findings):
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO,
                                    message=f"all {len(big)} parts are connected ({len(edges)} contacts, gap ≤ {gap_m * 1000:.0f} mm)"))
    findings.append(_ledger(names, parts, edges, exact, overlaps, planned, unresolved, gap_m))
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


def _nearest_supported(
    n: str,
    parts: dict[str, trimesh.Trimesh],
    support: set[str],
    exact: dict[tuple[str, str], PairDistance],
    cache: _PartCache,
    n_candidates: int = 3,
) -> PairDistance | None:
    """Exact distance from floating part ``n`` to its nearest supported part.

    Candidates are ranked by AABB lower bound; the closest few get an exact test
    (reusing the per-part BVHs in ``cache``).
    """
    if not support:
        return None
    ranked = sorted(support, key=lambda o: _aabb_gap(parts[n].bounds, parts[o].bounds))[:n_candidates]
    best: PairDistance | None = None
    for o in ranked:
        pd = exact.get((n, o)) or exact.get((o, n))
        if pd is None:
            pd = pair_distance(n, parts[n], o, parts[o], obj_a=cache.fcl(n), obj_b=cache.fcl(o))
            exact[(n, o)] = pd
        if best is None or pd.distance < best.distance:
            best = pd
    return best
