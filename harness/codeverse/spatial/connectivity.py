"""Connectivity gate: are all parts of the GLB physically attached?

Mechanism
---------
1. Load one world-space mesh per top-level node (``measure.part_meshes``).
2. For every part pair whose AABBs come within ``gap_m`` of each other compute
   the exact minimum surface distance (python-fcl via ``trimesh.collision``;
   sampled closest-point fallback when fcl is missing) and the closest points.
3. Contact graph: edge when distance ≤ ``gap_m``.  The *support component* is
   the connected component holding the ground-touching parts (lowest point at
   y ≤ gap) — or the largest component when nothing touches the ground.
4. Parts outside the support component are **floating** → ERROR with the
   nearest supported part and the exact translation vector that closes the gap.
5. Overlapping AABB pairs are probed for mutual containment of surface samples;
   a depth above ``PENETRATION_WARN_M`` → WARN, above ``PENETRATION_ERROR_M``
   → ERROR (parts are allowed to overlap by a hair for welding, not to pass
   through each other).
6. Tiny disconnected islands inside a single part → WARN (stray geometry).

All thresholds are module constants (meters) and keyword-overridable.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.conventions import CONTACT_GAP_M
from codeverse.spatial.measure import GlbLoadError, load_scene, part_meshes

GATE = "connectivity"
#: parts smaller than this (max extent) are ignored for floating checks (INFO only)
MIN_PART_SIZE_M = 0.01
#: penetration depth that is reported as WARN / ERROR
PENETRATION_WARN_M = 0.002
PENETRATION_ERROR_M = 0.010
#: fraction of one part's samples inside the other before we call it penetration
PENETRATION_MIN_FRACTION = 0.02
#: surface samples per part for containment / fallback distance tests
N_SAMPLES = 600
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


def _aabb_gap(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    """Axis-aligned lower bound of the distance between two meshes."""
    lo = np.maximum(a.bounds[0] - b.bounds[1], b.bounds[0] - a.bounds[1])
    return float(np.linalg.norm(np.maximum(lo, 0.0)))


def _fcl_available() -> bool:
    try:
        import fcl  # noqa: F401

        return True
    except Exception:
        return False


def pair_distance(name_a: str, a: trimesh.Trimesh, name_b: str, b: trimesh.Trimesh) -> PairDistance:
    """Exact (fcl) or sampled minimum distance between two meshes with closest points."""
    if _fcl_available():
        try:
            from trimesh.collision import CollisionManager

            cm = CollisionManager()
            cm.add_object("a", a)
            d, _name, data = cm.min_distance_single(b, return_name=True, return_data=True)
            pa, pb = data.point("a"), data.point("__external")
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


def penetration_depth(a: trimesh.Trimesh, b: trimesh.Trimesh, n: int = N_SAMPLES) -> tuple[float, float]:
    """(max depth, fraction of samples inside) of ``a``'s surface inside ``b`` and vice versa."""
    rng = np.random.default_rng(_RNG_SEED)
    worst_depth, worst_frac = 0.0, 0.0
    for src, dst in ((a, b), (b, a)):
        if not dst.is_watertight:  # containment is meaningless on open shells
            continue
        pts = _sample(src, n, rng)
        try:
            inside = dst.contains(pts)
        except Exception:
            continue
        frac = float(np.mean(inside)) if len(inside) else 0.0
        if frac <= 0.0:
            continue
        _, dist, _ = trimesh.proximity.closest_point(dst, pts[inside])
        worst_depth = max(worst_depth, float(np.max(dist)))
        worst_frac = max(worst_frac, frac)
    return worst_depth, worst_frac


def _components(names: list[str], edges: set[tuple[str, str]]) -> list[set[str]]:
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comps: dict[str, set[str]] = {}
    for n in names:
        comps.setdefault(find(n), set()).add(n)
    return list(comps.values())


def _fmt_vec(v: tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{x:+.4f}" for x in v) + ")"


def _island_findings(name: str, mesh: trimesh.Trimesh) -> list[GateFinding]:
    try:
        islands = mesh.split(only_watertight=False)
    except Exception:
        return []
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


def check_connectivity(
    glb: Path | str,
    *,
    gap_m: float = CONTACT_GAP_M,
    min_part_size_m: float = MIN_PART_SIZE_M,
    penetration_warn_m: float = PENETRATION_WARN_M,
    penetration_error_m: float = PENETRATION_ERROR_M,
) -> GateReport:
    """Run the connectivity gate on ``glb`` (see module docstring)."""
    t0 = time.time()
    findings: list[GateFinding] = []
    try:
        parts = {k: v for k, v in part_meshes(load_scene(glb)).items() if v is not None and len(v.faces)}
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
    for a, b in itertools.combinations(big, 2):
        lower = _aabb_gap(parts[a], parts[b])
        if lower > gap_m:
            continue
        pd = pair_distance(a, parts[a], b, parts[b])
        exact[(a, b)] = pd
        if pd.distance <= gap_m:
            edges.add((a, b))
        if lower == 0.0:  # AABBs overlap → possible penetration
            depth, frac = penetration_depth(parts[a], parts[b])
            if frac >= PENETRATION_MIN_FRACTION and depth > penetration_warn_m:
                sev = Severity.ERROR if depth > penetration_error_m else Severity.WARN
                findings.append(GateFinding(
                    gate=GATE, severity=sev, target=a,
                    message=f"'{a}' and '{b}' interpenetrate by ≈{depth * 1000:.1f} mm ({frac:.0%} of surface samples inside)",
                    fix_hint=f"shrink or move '{a}'/'{b}' so they overlap by ≤ {penetration_warn_m * 1000:.0f} mm (a hairline overlap is fine for welding)",
                    data={"other": b, "depth_m": depth, "fraction_inside": frac},
                ))
    # ---- components → floating parts
    comps = _components(big, edges)
    grounded = {n for n in big if float(parts[n].bounds[0][1]) <= gap_m}
    support_sets = [c for c in comps if c & grounded]
    if support_sets:
        support = max(support_sets, key=lambda c: sum(len(parts[n].faces) for n in c))
        for c in support_sets:
            support |= c
    else:
        support = max(comps, key=lambda c: sum(len(parts[n].faces) for n in c)) if comps else set()
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, message="no part touches the ground (y=0)",
                                    fix_hint="move the whole object down so its lowest point sits at y=0"))
    for n in big:
        if n in support:
            continue
        near = _nearest_supported(n, parts, support, exact)
        if near is None:
            findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=n,
                                        message=f"part '{n}' is floating: touches nothing", fix_hint=f"attach '{n}' to a neighbouring part"))
            continue
        other = near.b if near.a == n else near.a
        vec = near.gap_vector if near.a == n else tuple(-x for x in near.gap_vector)
        p_self = near.point_a if near.a == n else near.point_b
        p_other = near.point_b if near.a == n else near.point_a
        findings.append(GateFinding(
            gate=GATE, severity=Severity.ERROR, target=n,
            message=f"part '{n}' is floating: nearest supported part is '{other}' at {near.distance * 1000:.1f} mm",
            fix_hint=f"translate '{n}' by {_fmt_vec(vec)} m (or extend it by {near.distance * 1000:.1f} mm towards '{other}') so the surfaces touch",
            data={"nearest": other, "gap_m": near.distance, "gap_vector_m": list(vec),
                  "closest_point_self": list(p_self), "closest_point_other": list(p_other)},
        ))
    # ---- stray islands inside parts
    for n in big:
        findings.extend(_island_findings(n, parts[n]))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    if passed and len(big) > 1:
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO,
                                    message=f"all {len(big)} parts are connected ({len(edges)} contacts, gap ≤ {gap_m * 1000:.0f} mm)"))
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.time() - t0) * 1000))


def _nearest_supported(
    n: str,
    parts: dict[str, trimesh.Trimesh],
    support: set[str],
    exact: dict[tuple[str, str], PairDistance],
    n_candidates: int = 3,
) -> PairDistance | None:
    """Exact distance from floating part ``n`` to its nearest supported part.

    Candidates are ranked by AABB lower bound; the closest few get an exact test.
    """
    if not support:
        return None
    ranked = sorted(support, key=lambda o: _aabb_gap(parts[n], parts[o]))[:n_candidates]
    best: PairDistance | None = None
    for o in ranked:
        pd = exact.get((n, o)) or exact.get((o, n))
        if pd is None:
            pd = pair_distance(n, parts[n], o, parts[o])
            exact[(n, o)] = pd
        if best is None or pd.distance < best.distance:
            best = pd
    return best
