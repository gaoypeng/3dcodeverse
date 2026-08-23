"""Cross-sections of the canonical GLB: a labelled PNG + loop/area numbers.

``cross_section`` slices every (selected) part with an axis-aligned plane,
projects the resulting loops onto the two remaining axes and rasterises them
with PIL — one colour per part, a scale bar, axis labels and a legend.  The
numbers use shapely (even-odd fill) so a hollow tube reports its hollow ratio.

``slices_sheet`` tiles ``n`` evenly spaced sections along an axis into one grid
image — the cheap way to inspect interiors along a whole object.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

from codeverse.spatial.measure import GlbLoadError, load_scene, merged_mesh, part_meshes
from codeverse.spatial.registry import Observation

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


def _font(size: int = 13) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


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


def draw_section(data: SectionData, out_png: Path, *, size: int = 512, title: str = "") -> Path:
    """Rasterise ``data`` to a labelled PNG (plane axes, scale bar, legend)."""
    h_ax, v_ax = _PLANE_AXES[data.axis]
    hi, vi = _AXIS_INDEX[h_ax], _AXIS_INDEX[v_ax]
    img = Image.new("RGB", (size, size), (250, 250, 250))
    d = ImageDraw.Draw(img, "RGBA")
    font, small = _font(13), _font(11)
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
    ttl = title or f"section {data.axis} = {data.at_m:.3f} m"
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
    all_parts = {k: v for k, v in part_meshes(load_scene(glb)).items() if v is not None and len(v.faces)}
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
    t0 = time.time()
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
    return Observation(ok=True, text=text, numbers=numbers, images=[str(out)], duration_ms=int((time.time() - t0) * 1000))


def slices_sheet(glb: Path | str, axis: str, n: int, out_png: Path | str, *, parts: Sequence[str] | None = None, tile: int = 320) -> Observation:
    """``n`` evenly spaced sections along ``axis`` tiled into one labelled grid."""
    t0 = time.time()
    axis = axis.lower()
    if axis not in _AXIS_INDEX:
        return Observation.error(f"axis must be one of x|y|z, got {axis!r}")
    n = int(max(1, min(n, 12)))
    try:
        sel = _load_parts(glb, parts)
    except (GlbLoadError, KeyError) as e:
        return Observation.error(f"slices_sheet: {e}")
    if not sel:
        return Observation.error("slices_sheet: no mesh parts in the GLB")
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = min(n, 4)
    rows = int(np.ceil(n / cols))
    sheet = Image.new("RGB", (cols * tile, rows * tile), (255, 255, 255))
    numbers: dict[str, object] = {"axis": axis, "n": n, "slices": []}
    for i in range(n):
        frac = (i + 0.5) / n
        at_m = _resolve_at(sel, axis, frac, False)
        data = compute_section(sel, axis, at_m)
        tmp = out.with_name(f"{out.stem}_{i}.png")
        draw_section(data, tmp, size=tile, title=f"{axis}={at_m:.3f} m ({frac:.2f})")
        sheet.paste(Image.open(tmp), ((i % cols) * tile, (i // cols) * tile))
        tmp.unlink(missing_ok=True)
        numbers["slices"].append({"at_m": round(at_m, 4), "n_loops": data.n_loops,  # type: ignore[attr-defined]
                                  "area_m2": round(data.total_area_m2, 6), "hollow_ratio": round(data.hollow_ratio, 3)})
    sheet.save(out)
    summary = "; ".join(f"{s['at_m']:.3f}m: {s['n_loops']} loops, hollow {s['hollow_ratio']:.0%}" for s in numbers["slices"])  # type: ignore[index]
    return Observation(ok=True, text=f"{n} sections along {axis}: {summary}", numbers=numbers, images=[str(out)],
                       duration_ms=int((time.time() - t0) * 1000))
