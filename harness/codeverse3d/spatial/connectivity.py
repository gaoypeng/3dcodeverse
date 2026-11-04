"""Connectivity gate: are all parts of the GLB physically attached?"""

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
PlannedEdge = tuple[str, "str | Sequence[str]"]
MIN_PART_SIZE_M = 0.01
PENETRATION_WARN_M = 0.002
PENETRATION_ERROR_M = 0.010
PENETRATION_MIN_FRACTION = 0.02
PENETRATION_MIN_INSIDE = 4
PENETRATION_MIN_LOCAL_FRACTION = 0.2
THROUGH_FAR_SIDE_RATIO = 0.9
N_SAMPLES = 600
LOCAL_MIN_SAMPLES = 128
LOCAL_EXTRA_SAMPLES = 512
LOCAL_MAX_VERTICES = 1500
_REGION_HAIR_M = 0.001
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
    """The deepest direction of an overlap probe."""

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
        """This direction's 2 · depth / thickness of the container, clipped to [0, 2]."""
        if self.thickness_m <= 0.0:
            return 0.0
        return float(min(2.0, max(0.0, 2.0 * self.depth_m / self.thickness_m)))

    def as_data(self) -> dict[str, Any]:
        return {"entering": self.entering, "container": self.container, "depth_m": self.depth_m,
                "fraction_inside": self.fraction_inside, "local_fraction": self.local_fraction,
                "inside_count": self.inside_count, "through_ratio": self.through_ratio, "thickness_m": self.thickness_m}


class _PartCache:
    """Per-part collision geometry built once and shared across every pair."""

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
    """Exact (fcl) minimum distance between two meshes with closest points."""
    oa = obj_a if obj_a is not None else fcl_collision_object(a)
    ob = obj_b if obj_b is not None else fcl_collision_object(b)
    req = collide._fcl.DistanceRequest(enable_nearest_points=True)
    res = collide._fcl.DistanceResult()
    d = collide._fcl.distance(oa, ob, req, res)
    pa, pb = res.nearest_points
    return PairDistance(name_a, name_b, float(max(d, 0.0)), tuple(map(float, pa)), tuple(map(float, pb)))


def _sample(mesh: trimesh.Trimesh, n: int, rng: np.random.Generator) -> np.ndarray:
    """Deterministic surface samples (seeded)."""
    pts, _ = trimesh.sample.sample_surface(mesh, n, seed=int(rng.integers(1 << 30)))
    return np.asarray(pts, dtype=float)


def part_thickness(mesh: trimesh.Trimesh) -> float:
    """Thinnest extent of ``mesh``."""
    v = np.asarray(mesh.vertices, dtype=float)
    ext = float(np.min(mesh.extents))
    if len(v) < 4:
        return ext
    c = v - v.mean(axis=0)
    _, axes = np.linalg.eigh(c.T @ c)
    p = c @ axes
    return float(min(ext, np.min(p.max(axis=0) - p.min(axis=0))))


def _inside_depth(islands: list[trimesh.Trimesh], pts: np.ndarray) -> tuple[np.ndarray, float]:
    """(mask of ``pts`` inside one of ``islands``, max distance of those to the surface)."""
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
    """LOCAL_EXTRA_SAMPLES surface samples of ``src`` inside the box."""
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
    """``src``'s surface inside ``dst``."""
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
    """Mutual containment of ``a`` and ``b``, both directions."""
    cache = cache if cache is not None else _PartCache({name_a: a, name_b: b})
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
            data={"kind": "stray_islands", "islands": len(islands), "tiny_islands": len(tiny)},
        ))
    return out


def _penetration_finding(a: str, b: str, pen: Penetration, warn_m: float, error_m: float) -> GateFinding:
    through = pen.through_ratio
    sev = (Severity.ERROR if pen.depth_m > error_m and pen.fraction_inside >= PENETRATION_MIN_FRACTION
           else Severity.WARN)
    msg = (f"'{a}' and '{b}' interpenetrate by ≈{pen.depth_m * 1000:.1f} mm "
           f"({pen.fraction_inside:.0%} of surface samples inside, {pen.local_fraction:.0%} where they meet)")
    if through >= 0.5:
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
    """The per-pair table ``check_connectivity`` computes."""
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
    """Run the connectivity gate on ``glb`` (see module docstring)."""
    t0 = time.time()
    up = "z" if language_frame(language) is Frame.Z_UP_NEG_Y_FRONT else "y"
    findings: list[GateFinding] = []
    try:
        parts = solid_parts(glb)
    except GlbLoadError as e:
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, message=str(e), fix_hint="run `build` first"))
        return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))
    if not parts:
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, message="GLB has no mesh parts",
                                    fix_hint="the build exported nothing — check the code creates geometry"))
        return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))

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
    cache = _PartCache(parts)
    for a, b in itertools.combinations(big, 2):
        lower = _aabb_gap(parts[a].bounds, parts[b].bounds)
        if lower > gap_m:
            continue
        pd = pair_distance(a, parts[a], b, parts[b], obj_a=cache.fcl(a), obj_b=cache.fcl(b))
        exact[(a, b)] = pd
        if pd.distance <= gap_m:
            edges.add((a, b))
        if lower == 0.0:  # AABBs overlap → possible penetration
            seeds = np.array([pd.point_a, pd.point_b], dtype=float) if pd.distance <= 0.0 else None
            if seeds is not None:
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
    grounded = {n for n in big if abs(float(parts[n].bounds[0][1])) <= gap_m}
    support_sets = [c for c in comps if c & grounded]
    if support_sets:
        support = set(max(support_sets, key=lambda c: sum(len(parts[n].faces) for n in c)))
    else:
        support = min(comps, key=lambda c: (round(min(float(parts[n].bounds[0][1]) for n in c), 6),
                                            -sum(len(parts[n].faces) for n in c))) if comps else set()
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, message=f"no part touches the ground ({up}=0)",
                                    data={"kind": "no_ground"},
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
            fix_hint=f"translate '{n}' by {fmt_vec(vec, 4)} m ({frame_label(language)}) — or extend it by "
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
    """Exact distance from floating part ``n`` to its nearest supported part."""
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
