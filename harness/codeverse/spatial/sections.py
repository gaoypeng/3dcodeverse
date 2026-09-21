"""Cross-sections of the canonical GLB: a labelled PNG + loop/area numbers.

``cross_section`` slices every (selected) part with an axis-aligned plane,
projects the resulting loops onto the two remaining axes and rasterises them
with PIL — one colour per part, a scale bar, axis labels and a legend.  The
numbers use shapely (even-odd fill) so a hollow tube reports its hollow ratio.

``judge_slices`` (D48) renders the judge's conditional interior evidence: two
vertical centre slices with per-part fills, where ONLY connectivity-gate-ERROR
part pairs get a red hatch, written with a typed :class:`SliceManifest`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from codeverse.spatial.measure import GlbLoadError, merged_mesh, solid_parts
from codeverse.spatial.registry import Observation
from codeverse.spatial.sheet import load_font

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
#: which two axes are drawn (horizontal, vertical) for a slicing axis
_PLANE_AXES = {"x": ("z", "y"), "y": ("x", "z"), "z": ("x", "y")}
_PALETTE = [
    (230, 80, 60), (60, 130, 230), (50, 170, 90), (240, 160, 30), (150, 80, 200),
    (30, 180, 190), (200, 60, 150), (120, 120, 40), (90, 90, 90), (250, 110, 140),
]


@dataclass
class SectionData:
    axis: str
    at_m: float
    loops: dict[str, list[np.ndarray]] = field(default_factory=dict)  # part → list of (N,2) loops
    n_loops: int = 0
    total_area_m2: float = 0.0
    hollow_ratio: float = 0.0
    bbox_min: np.ndarray | None = None
    bbox_max: np.ndarray | None = None


def _section_loops(mesh: trimesh.Trimesh, axis: str, at: float) -> list[np.ndarray]:
    """Closed 2D loops (in plane axes) of ``mesh`` cut at ``axis = at``."""
    normal = np.zeros(3)
    normal[_AXIS_INDEX[axis]] = 1.0
    origin = normal * at
    try:
        path = mesh.section(plane_origin=origin, plane_normal=normal)
    except Exception:
        return []
    if path is None:
        return []
    h, v = (_AXIS_INDEX[a] for a in _PLANE_AXES[axis])
    loops = []
    for poly in path.discrete:
        pts = np.asarray(poly)[:, [h, v]]
        if len(pts) >= 3:
            loops.append(pts)
    return loops


def _even_odd_area(loops: Sequence[np.ndarray]) -> tuple[float, float]:
    """(filled area, hollow ratio) using even-odd fill of all loops (shapely)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = []
    for lp in loops:
        try:
            p = Polygon(lp)
            if not p.is_valid:
                p = p.buffer(0)
            if p.area > 0:
                polys.append(p)
        except Exception:
            continue
    if not polys:
        return 0.0, 0.0
    polys.sort(key=lambda p: -p.area)
    filled = polys[0]
    for p in polys[1:]:
        filled = filled.symmetric_difference(p)
    outer = unary_union(polys).area
    hollow = 1.0 - filled.area / outer if outer > 0 else 0.0
    return float(filled.area), float(max(0.0, min(1.0, hollow)))


def compute_section(parts: dict[str, trimesh.Trimesh], axis: str, at_m: float) -> SectionData:
    """Slice ``parts`` (world-space meshes) at ``axis = at_m``."""
    data = SectionData(axis=axis, at_m=at_m)
    whole = merged_mesh(parts)  # type: ignore[arg-type]
    if whole is not None:
        data.bbox_min, data.bbox_max = whole.bounds
    all_loops: list[np.ndarray] = []
    for name, mesh in parts.items():
        loops = _section_loops(mesh, axis, at_m)
        if loops:
            data.loops[name] = loops
            all_loops.extend(loops)
    data.n_loops = len(all_loops)
    data.total_area_m2, data.hollow_ratio = _even_odd_area(all_loops)
    return data


def draw_section(data: SectionData, out_png: Path, *, size: int = 512) -> Path:
    """Rasterise ``data`` to a labelled PNG (plane axes, scale bar, legend)."""
    h_ax, v_ax = _PLANE_AXES[data.axis]
    hi, vi = _AXIS_INDEX[h_ax], _AXIS_INDEX[v_ax]
    img = Image.new("RGB", (size, size), (250, 250, 250))
    d = ImageDraw.Draw(img, "RGBA")
    font, small = load_font(13), load_font(11)
    margin = 36
    if data.bbox_min is None:
        d.text((margin, margin), "empty model", fill=(0, 0, 0), font=font)
        img.save(out_png)
        return out_png
    lo = np.array([data.bbox_min[hi], data.bbox_min[vi]])
    hi_ = np.array([data.bbox_max[hi], data.bbox_max[vi]])
    span = float(max(np.max(hi_ - lo), 1e-6))
    scale = (size - 2 * margin) / span
    centre = (lo + hi_) / 2

    def to_px(p: np.ndarray) -> tuple[float, float]:
        x = size / 2 + (p[0] - centre[0]) * scale
        y = size / 2 - (p[1] - centre[1]) * scale
        return (float(x), float(y))

    # ground/axes reference (dashed bbox of the whole object in this plane)
    (x0b, y0b), (x1b, y1b) = to_px(lo), to_px(hi_)
    d.rectangle([min(x0b, x1b), min(y0b, y1b), max(x0b, x1b), max(y0b, y1b)], outline=(190, 190, 190), width=1)
    legend = []
    for i, (name, loops) in enumerate(data.loops.items()):
        col = _PALETTE[i % len(_PALETTE)]
        for lp in loops:
            pts = [to_px(p) for p in lp]
            if len(pts) >= 3:
                d.polygon(pts, fill=col + (70,), outline=col + (255,))
        legend.append((name, col))
    # legend (top-left), capped
    y = 6
    for name, col in legend[:12]:
        d.rectangle([6, y + 2, 16, y + 12], fill=col)
        d.text((20, y), name, fill=(20, 20, 20), font=small)
        y += 14
    if len(legend) > 12:
        d.text((20, y), f"… +{len(legend) - 12} parts", fill=(20, 20, 20), font=small)
    # title + axes
    ttl = f"section {data.axis} = {data.at_m:.3f} m"
    d.text((size - 6 - d.textlength(ttl, font=font), 6), ttl, fill=(0, 0, 0), font=font)
    d.text((size - 16, size / 2 - 8), h_ax, fill=(120, 0, 0), font=font)
    d.text((size / 2 - 4, 20), v_ax, fill=(0, 90, 0), font=font)
    # scale bar: nice length ≈ 1/4 of span
    nice = 10 ** np.floor(np.log10(span / 4))
    bar = float(nice * max(1, round(span / 4 / nice)))
    x0, y0 = margin, size - margin / 2
    d.line([(x0, y0), (x0 + bar * scale, y0)], fill=(0, 0, 0), width=2)
    d.text((x0, y0 - 16), f"{bar * 100:g} cm", fill=(0, 0, 0), font=small)
    stats = f"{data.n_loops} loops · area {data.total_area_m2 * 1e4:.1f} cm² · hollow {data.hollow_ratio:.0%}"
    d.text((size - 6 - d.textlength(stats, font=small), size - 18), stats, fill=(0, 0, 0), font=small)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png


def _resolve_at(parts: dict[str, trimesh.Trimesh], axis: str, at: float, absolute: bool) -> float:
    whole = merged_mesh(parts)  # type: ignore[arg-type]
    if whole is None:
        return at
    i = _AXIS_INDEX[axis]
    lo, hi = float(whole.bounds[0][i]), float(whole.bounds[1][i])
    if absolute:
        return at
    return lo + (hi - lo) * float(np.clip(at, 0.0, 1.0))


def _load_parts(glb: Path | str, parts: Sequence[str] | None) -> dict[str, trimesh.Trimesh]:
    all_parts = solid_parts(glb)
    if parts:
        sel = {k: v for k, v in all_parts.items() if k in set(parts)}
        missing = [p for p in parts if p not in all_parts]
        if missing:
            raise KeyError(f"unknown part(s) {missing}; available: {sorted(all_parts)}")
        return sel
    return all_parts


def cross_section(
    glb: Path | str,
    axis: str,
    at: float,
    out_png: Path | str,
    *,
    parts: Sequence[str] | None = None,
    absolute: bool = False,
    size: int = 512,
) -> Observation:
    """Slice the GLB at ``axis = at`` (fraction of the bbox, or meters if ``absolute``)."""
    axis = axis.lower()
    if axis not in _AXIS_INDEX:
        return Observation.error(f"axis must be one of x|y|z, got {axis!r}")
    try:
        sel = _load_parts(glb, parts)
    except (GlbLoadError, KeyError) as e:
        return Observation.error(f"cross_section: {e}")
    if not sel:
        return Observation.error("cross_section: no mesh parts in the GLB")
    at_m = _resolve_at(sel, axis, at, absolute)
    data = compute_section(sel, axis, at_m)
    out = draw_section(data, Path(out_png), size=size)
    per_part = {n: len(loops) for n, loops in data.loops.items()}
    cut = ", ".join(f"{n}({k})" for n, k in list(per_part.items())[:12]) or "nothing"
    text = (
        f"cross-section {axis}={at_m:.3f} m ({'absolute' if absolute else f'{at:.2f} of bbox'}): "
        f"{data.n_loops} loop(s), filled area {data.total_area_m2 * 1e4:.1f} cm², hollow ratio {data.hollow_ratio:.0%}. "
        f"Parts cut (loops): {cut}. Plane axes: {_PLANE_AXES[axis][0]} horizontal, {_PLANE_AXES[axis][1]} vertical."
    )
    numbers = {"axis": axis, "at_m": round(at_m, 4), "n_loops": data.n_loops,
               "total_area_m2": round(data.total_area_m2, 6), "hollow_ratio": round(data.hollow_ratio, 3),
               "parts_cut": per_part}
    return Observation(ok=True, text=text, numbers=numbers, images=[str(out)])


# ===================================================================== judge slices (D48)
#: the two centre planes the judge slice channel cuts (name → plane-normal axis index,
#: horizontal axis index, human title, axes note).  Vertical is always Y (up).
JUDGE_SLICE_PLANES: dict[str, tuple[int, int, str, str]] = {
    "front_back": (0, 2, "front-back",
                   "horizontal = front-back (Z, front left), vertical = up (Y); cut at centre x"),
    "left_right": (2, 0, "left-right",
                   "horizontal = left-right (X), vertical = up (Y); cut at centre z"),
}
#: F1: only part pairs the connectivity gate measured as ERROR get this fill + hatch
_OVERLAP_FACE = "#ff1a1a"
#: legend rows (instance groups) drawn before the "… +n more parts" row
JUDGE_SLICE_LEGEND_MAX = 16
#: F4: a slice whose effective section area is under this fraction of the cut parts'
#: projected-silhouette area is dropped (the plane mostly misses the object)
DEGENERATE_SLICE_FRAC = 0.05
_HULL_SAMPLE = 4000  # vertex subsample for projected-silhouette convex hulls (seeded rng)


class SliceOverlapPair(BaseModel):
    """One measured section-polygon intersection between two parts on a slice."""

    a: str
    b: str
    area_m2: float


class JudgeSlice(BaseModel):
    """One rendered (or dropped) judge slice; ``png`` is a file name beside the manifest."""

    name: str
    rendered: bool
    png: str = ""
    reason: str = ""
    parts_filled: int = 0
    parts_outline_only: int = 0
    effective_frac: float = 0.0
    hatched_pairs: list[SliceOverlapPair] = Field(default_factory=list)
    plain_pairs: list[SliceOverlapPair] = Field(default_factory=list)


class SliceManifest(BaseModel):
    """What ``judge_slices`` rendered and why — the provenance record beside the PNGs.

    ``hatched_pairs`` (F1: gate-ERROR only) vs ``plain_pairs`` (every other in-plane
    overlap, drawn as a darkened blend with no callout) is what makes a judge's
    "visible in slice n" citation auditable after the fact.
    """

    glb: str
    slices: list[JudgeSlice] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_pairs: list[tuple[str, str]] = Field(default_factory=list)

    def rendered(self) -> list[JudgeSlice]:
        return [s for s in self.slices if s.rendered]


def _judge_loops_2d(mesh: trimesh.Trimesh, origin: np.ndarray, axis: int, horiz: int,
                    tol: float) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Section ``mesh`` with the axis-aligned plane; return (closed, open) 2D polylines
    (closed = endpoints within ``tol``; vertical axis is always Y)."""
    normal = np.zeros(3)
    normal[axis] = 1.0
    try:
        path3 = mesh.section(plane_origin=origin, plane_normal=normal)
    except Exception:  # noqa: BLE001 — a part trimesh cannot section is outline-less, not fatal
        path3 = None
    if path3 is None:
        return [], []
    closed: list[np.ndarray] = []
    open_: list[np.ndarray] = []
    for line in path3.discrete:
        pts = np.asarray(line)
        if len(pts) < 2:
            continue
        p2 = np.column_stack([pts[:, horiz], pts[:, 1]])
        if len(p2) >= 4 and np.linalg.norm(p2[0] - p2[-1]) <= tol:
            closed.append(p2)
        else:
            open_.append(p2)
    return closed, open_


def _even_odd_polygon(loops: Sequence[np.ndarray]) -> Any:
    """Combine closed loops with the even-odd rule (outer boundaries + holes) → shapely
    Polygon/MultiPolygon, or ``None`` when nothing fillable remains."""
    from functools import reduce

    from shapely.geometry import Polygon
    from shapely.validation import make_valid

    polys = []
    for lp in loops:
        try:
            p = make_valid(Polygon(lp))
            if p.area > 0:
                polys.append(p)
        except Exception:  # noqa: BLE001 — skip degenerate loops
            continue
    if not polys:
        return None
    try:
        geom = reduce(lambda a, b: a.symmetric_difference(b), polys)
    except Exception:  # noqa: BLE001
        return None
    if geom.is_empty:
        return None
    geom = make_valid(geom)
    return geom if geom.geom_type in ("Polygon", "MultiPolygon") else None


def _iter_polys(geom: Any):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            yield from _iter_polys(g)


def _slice_patch(geom: Any, **kw: Any) -> Any:
    """Shapely polygon(s) → one matplotlib PathPatch (holes included), or ``None``."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath

    verts: list[Any] = []
    codes: list[Any] = []
    for poly in _iter_polys(geom):
        for ring in [poly.exterior, *poly.interiors]:
            pts = np.asarray(ring.coords)
            verts.extend(pts)
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 2) + [MplPath.CLOSEPOLY])
    if not verts:
        return None
    return PathPatch(MplPath(np.asarray(verts), codes), **kw)


def _hull_area(pts2: np.ndarray) -> float:
    """Convex-hull area of a 2D point set (0.0 for degenerate sets; seeded subsample)."""
    pts2 = np.asarray(pts2, dtype=float)
    if len(pts2) < 3:
        return 0.0
    if len(pts2) > _HULL_SAMPLE:
        idx = np.random.default_rng(0).choice(len(pts2), _HULL_SAMPLE, replace=False)
        pts2 = pts2[idx]
    try:
        from shapely.geometry import MultiPoint

        return float(MultiPoint(pts2).convex_hull.area)
    except Exception:  # noqa: BLE001 — collinear/degenerate sets
        return 0.0


def _mix_dark(ca: Sequence[float], cb: Sequence[float]) -> tuple[float, ...]:
    """Darkened blend of two RGBA colors (plain non-ERROR overlap fill)."""
    a, b = np.asarray(ca[:3]), np.asarray(cb[:3])
    return tuple(0.55 * (a + b) / 2.0) + (1.0,)


def _nice_scale(span: float) -> float:
    """A 1/2/5×10^k length ≈ a quarter of the horizontal span."""
    target = span / 4.0
    k = np.floor(np.log10(target))
    for mult in (5.0, 2.0, 1.0):
        v = mult * 10.0**k
        if v <= target:
            return float(v)
    return float(10.0**k)


def _fmt_len(m: float) -> str:
    if m >= 1.0:
        return f"{m:g} m"
    if m >= 0.01:
        return f"{m * 100:g} cm"
    return f"{m * 1000:g} mm"


def judge_slices(
    glb: Path | str,
    error_pairs: Iterable[tuple[str, str]],
    out_dir: Path | str,
    planes: tuple[str, ...] = ("front_back", "left_right"),
) -> SliceManifest:
    """Render the judge's interior cross-section slices (D48) → :class:`SliceManifest`.

    Two vertical centre slices of the canonical GLB (Y-up, meters), one PNG each
    (``slice_<name>.png``, ~768 px, white background): per-part fills with a legend
    (instance groups share a row, capped at :data:`JUDGE_SLICE_LEGEND_MAX`), a scale
    bar, and a neutral title.  ``error_pairs`` are the connectivity gate's ERROR-level
    penetration part pairs (order inside a pair does not matter):

    * F1 — ONLY those pairs get the red fill + white hatch; every other section
      intersection (planned joins, sub-threshold welds) is a plain darkened blend of
      the two part colors, no callout.  A hairline contact sliver (thinner than
      ~2×erosion everywhere, tuned near the gate's 10 mm ERROR line) is not drawn.
    * F2 — the legend names the hatch factually: "measured overlap (gate ERROR)".
    * F4 — a degenerate slice (effective section area under
      :data:`DEGENERATE_SLICE_FRAC` of the cut parts' projected silhouette — the plane
      mostly misses the object) is dropped, not rendered, so a case carries 0–2 slices.

    A non-watertight / non-loop section falls back to raw outline strokes for that
    part (no fill, no overlap test).  Deterministic (seeded subsampling, Agg backend);
    the manifest is written beside the PNGs as ``manifest.json`` with ``png`` fields
    holding bare file names.  Needs matplotlib + shapely (the ``mesh`` extra): an
    ImportError propagates to the caller.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    errset = {frozenset(p) for p in (error_pairs or ()) if len(set(p)) == 2}
    manifest = SliceManifest(glb=str(glb),
                             error_pairs=[tuple(sorted(p)) for p in sorted(errset, key=sorted)])

    def _done() -> SliceManifest:
        (out_dir / "manifest.json").write_text(manifest.model_dump_json(indent=1))
        return manifest

    try:
        parts = solid_parts(glb)
    except Exception as e:  # noqa: BLE001 — a bad GLB yields an empty manifest, not a crash
        manifest.errors.append(f"load: {type(e).__name__}: {e}")
        return _done()
    if not parts:
        manifest.errors.append("no solid parts")
        return _done()

    from codeverse.spatial.measure import instance_groups

    bounds = np.array([[min(p.bounds[0][i] for p in parts.values()) for i in range(3)],
                       [max(p.bounds[1][i] for p in parts.values()) for i in range(3)]])
    centre = bounds.mean(axis=0)
    scale = float((bounds[1] - bounds[0]).max())
    tol = max(scale * 1e-5, 1e-9)
    area_eps = (1e-3 * scale) ** 2
    # ignore hairline contact slivers (planned joins touch or overlap by design): keep only
    # overlap regions at least ~2·erode thick somewhere — tuned near the connectivity
    # gate's ~10 mm interpenetration ERROR threshold (PENETRATION_ERROR_M)
    erode = max(0.002, 5e-4 * scale)

    # one color per instance group so 40-part objects stay legible
    groups = instance_groups(list(parts.keys()))
    group_of = {m: g for g, members in groups.items() for m in members}
    cmap = plt.get_cmap("tab20")
    color_of_group = {g: cmap(i % 20) for i, g in enumerate(groups)}

    for name in planes:
        axis, horiz, human, axes_note = JUDGE_SLICE_PLANES[name]
        sections: dict[str, Any] = {}   # part → shapely polygon
        strokes: dict[str, list[np.ndarray]] = {}   # part → open/unfillable polylines
        for pname, mesh in parts.items():
            closed, open_ = _judge_loops_2d(mesh, centre, axis, horiz, tol)
            geom = _even_odd_polygon(closed) if closed else None
            if geom is not None:
                sections[pname] = geom
                if open_:
                    strokes[pname] = open_
            elif closed or open_:
                strokes[pname] = closed + open_  # non-watertight fallback: raw outlines
        if not sections and not strokes:
            manifest.slices.append(JudgeSlice(name=name, rendered=False, reason="plane cuts nothing"))
            continue

        # F4: degenerate-slice check — effective section area (filled polygons + convex
        # hulls of outline-only stroke sets, so full-size open sections still count) vs
        # the cut parts' projected-silhouette area on the slice plane.
        filled_area = sum(g.area for g in sections.values())
        stroke_hull_area = sum(
            _hull_area(np.vstack(lines)) for p, lines in strokes.items() if p not in sections)
        effective_area = filled_area + stroke_hull_area
        proj_area = 0.0
        for pname in sorted(set(sections) | set(strokes)):
            v = np.asarray(parts[pname].vertices)
            proj_area += _hull_area(np.column_stack([v[:, horiz], v[:, 1]]))
        frac = effective_area / proj_area if proj_area > 0 else 0.0
        if frac < DEGENERATE_SLICE_FRAC:
            manifest.slices.append(JudgeSlice(
                name=name, rendered=False, effective_frac=round(frac, 4),
                reason=f"degenerate: effective section area {frac:.1%} of cut-part "
                       f"projection (< {DEGENERATE_SLICE_FRAC:.0%}) — mostly out-of-plane"))
            continue

        # measured overlaps: pairwise intersection of filled section polygons.
        # F1: only pairs the connectivity gate measured as ERROR-level get the red hatch.
        pnames = list(sections)
        overlaps: list[tuple[str, str, Any]] = []
        plain_overlaps: list[tuple[str, str, Any]] = []
        for i in range(len(pnames)):
            for j in range(i + 1, len(pnames)):
                a, b = pnames[i], pnames[j]
                try:
                    inter = sections[a].intersection(sections[b])
                except Exception:  # noqa: BLE001 — shapely topology errors on sick geometry
                    continue
                if inter.is_empty or inter.area <= area_eps:
                    continue
                try:
                    if inter.buffer(-erode).is_empty:
                        continue  # thinner than ~2·erode everywhere: contact sliver
                except Exception:  # noqa: BLE001
                    pass
                (overlaps if frozenset((a, b)) in errset else plain_overlaps).append((a, b, inter))

        fig, ax = plt.subplots(figsize=(7.68, 7.68), dpi=100)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        drawn_groups: dict[str, bool] = {}
        for pname, geom in sections.items():
            g = group_of[pname]
            patch = _slice_patch(geom, facecolor=color_of_group[g], edgecolor="#333333",
                                 linewidth=0.6, alpha=0.9, zorder=2)
            if patch is not None:
                ax.add_patch(patch)
                drawn_groups[g] = drawn_groups.get(g, False)
        for pname, lines in strokes.items():
            g = group_of[pname]
            for lp in lines:
                ax.plot(lp[:, 0], lp[:, 1], color=color_of_group[g], linewidth=1.4, zorder=3)
            drawn_groups[g] = True  # True = outline-only somewhere
        for a, b, inter in plain_overlaps:  # F1: not gate-ERROR — darkened blend, no callout
            fill = _slice_patch(inter, facecolor=_mix_dark(color_of_group[group_of[a]],
                                                           color_of_group[group_of[b]]),
                                edgecolor="none", linewidth=0.0, zorder=4)
            if fill is not None:
                ax.add_patch(fill)
        for _a, _b, inter in overlaps:
            fill = _slice_patch(inter, facecolor=_OVERLAP_FACE, edgecolor="#8a0000",
                                linewidth=1.2, zorder=5)
            if fill is not None:
                ax.add_patch(fill)
            hatch = _slice_patch(inter, facecolor="none", edgecolor="white",
                                 hatch="////", linewidth=0.0, zorder=6)
            if hatch is not None:
                ax.add_patch(hatch)

        # frame the drawing on the slice plane's full object extent
        h_lo, h_hi = bounds[0][horiz], bounds[1][horiz]
        v_lo, v_hi = bounds[0][1], bounds[1][1]
        pad = 0.06 * max(h_hi - h_lo, v_hi - v_lo, 1e-6)
        ax.set_xlim(h_lo - pad, h_hi + pad)
        ax.set_ylim(v_lo - pad - 0.08 * (v_hi - v_lo + 2 * pad), v_hi + pad)
        ax.set_aspect("equal")
        ax.axis("off")

        # scale bar, bottom-left
        bar = _nice_scale(h_hi - h_lo + 2 * pad)
        y0 = v_lo - pad - 0.03 * (v_hi - v_lo + 2 * pad)
        ax.plot([h_lo, h_lo + bar], [y0, y0], color="black", linewidth=3, zorder=6)
        ax.text(h_lo + bar / 2, y0 - 0.015 * (v_hi - v_lo + 2 * pad), _fmt_len(bar),
                ha="center", va="top", fontsize=10, color="black")

        handles = []
        for g in list(drawn_groups)[:JUDGE_SLICE_LEGEND_MAX]:
            label = g if len(groups[g]) == 1 else f"{g} (x{len(groups[g])})"
            if drawn_groups[g]:
                handles.append(Line2D([], [], color=color_of_group[g], linewidth=2,
                                      label=label + " [outline only — not filled; NOT a hole]"))
            else:
                handles.append(Patch(facecolor=color_of_group[g], edgecolor="#333333", label=label))
        if len(drawn_groups) > JUDGE_SLICE_LEGEND_MAX:
            handles.append(Patch(facecolor="white", edgecolor="white",
                                 label=f"… +{len(drawn_groups) - JUDGE_SLICE_LEGEND_MAX} more parts"))
        if overlaps:  # F2: factual wording, only ever true for gate-ERROR pairs
            handles.append(Patch(facecolor=_OVERLAP_FACE, edgecolor="white",
                                 hatch="////", label="measured overlap (gate ERROR)"))
        ax.legend(handles=handles, loc="upper right", fontsize=7, framealpha=0.9,
                  title=f"cross-section {human}", title_fontsize=8)
        ax.set_title(f"cross-section {human} — interior view\n{axes_note}", fontsize=9)

        png = out_dir / f"slice_{name}.png"
        fig.savefig(png, facecolor="white", bbox_inches="tight", dpi=130)
        plt.close(fig)
        manifest.slices.append(JudgeSlice(
            name=name, rendered=True, png=png.name,
            parts_filled=len(sections), parts_outline_only=len(strokes),
            effective_frac=round(frac, 4),
            hatched_pairs=[SliceOverlapPair(a=a, b=b, area_m2=round(inter.area, 8))
                           for a, b, inter in overlaps],
            plain_pairs=[SliceOverlapPair(a=a, b=b, area_m2=round(inter.area, 8))
                         for a, b, inter in plain_overlaps]))
    return _done()
