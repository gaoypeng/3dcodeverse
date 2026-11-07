"""Silhouette comparison between a render and a reference image."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import RenderView
from codeverse3d.contracts.spec import ReferenceImage, Spec
from codeverse3d.conventions import views_by_preference

MASK_SIZE = 256
BORDER_TOL_MIN = 10.0
BORDER_TOL_MAX = 40.0
BORDER_TOL_K = 4.0
_MIN_FILL = 0.002
_MAX_BBOX_COVER = 0.98


def _load_rgba(path: Path | str, size: int) -> np.ndarray:
    img = Image.open(path).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    return np.asarray(img, dtype=np.float32)


def _flood_outside(bg_like: np.ndarray) -> np.ndarray:
    """Pixels reachable from the border through ``bg_like`` (4-connected)."""
    try:
        from scipy import ndimage

        labels, _ = ndimage.label(bg_like)
        border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        border = border[border > 0]
        return np.isin(labels, border)
    except Exception:
        pass
    h, w = bg_like.shape
    seen = np.zeros_like(bg_like, dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if bg_like[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg_like[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and bg_like[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    return seen


def foreground_mask(path: Path | str, size: int = MASK_SIZE, tol: float | None = None) -> np.ndarray:
    """Boolean foreground mask (≤ size², aspect preserved)."""
    rgba = _load_rgba(path, size)
    alpha = rgba[..., 3]
    if alpha.min() < 250:  # has real transparency → trust it
        return alpha > 8
    rgb = rgba[..., :3]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    bg = np.median(border, axis=0)
    if tol is None:
        spread = float(np.linalg.norm(border - bg, axis=-1).std())
        tol = float(np.clip(BORDER_TOL_MIN + BORDER_TOL_K * spread, BORDER_TOL_MIN, BORDER_TOL_MAX))
    dist = np.linalg.norm(rgb - bg, axis=-1)
    bg_like = dist <= tol
    outside = _flood_outside(bg_like)
    return ~outside


def _bbox_cover(mask: np.ndarray) -> float:
    """Fraction of the frame the mask's BOUNDING BOX spans (1.0 = corner to corner)."""
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return 0.0
    h, w = mask.shape
    return float(((ys.max() - ys.min() + 1) / h) * ((xs.max() - xs.min() + 1) / w))


def _normalise(mask: np.ndarray, size: int = MASK_SIZE) -> tuple[np.ndarray, float]:
    """Crop to the mask bbox and scale into a size² canvas."""
    ys, xs = np.nonzero(mask)
    canvas = np.zeros((size, size), dtype=bool)
    if len(ys) == 0:
        return canvas, 0.0
    crop = mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    h, w = crop.shape
    aspect = w / h if h else 0.0
    s = (size - 2) / max(h, w)
    nh, nw = max(1, int(round(h * s))), max(1, int(round(w * s)))
    img = Image.fromarray((crop * 255).astype(np.uint8)).resize((nw, nh), Image.BILINEAR)
    arr = np.asarray(img) > 127
    y0, x0 = (size - nh) // 2, (size - nw) // 2
    canvas[y0: y0 + nh, x0: x0 + nw] = arr
    return canvas, aspect


def _diff_image(a: np.ndarray, b: np.ndarray, out: Path) -> Path:
    """Overlay."""
    h, w = a.shape
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    both = a & b
    img[both] = (150, 150, 150)
    img[a & ~b] = (220, 60, 60)
    img[b & ~a] = (60, 90, 220)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(out)
    return out


def compare_silhouette(render_png: Path | str, reference_png: Path | str, *, diff_png: Path | str | None = None) -> dict[str, Any]:
    """IoU + fills + aspect error between a render and a reference image."""
    ref_mask = foreground_mask(reference_png)
    ren_mask = foreground_mask(render_png)
    ref_fill = float(ref_mask.mean())
    ren_fill = float(ren_mask.mean())
    ref_n, ref_aspect = _normalise(ref_mask)
    ren_n, ren_aspect = _normalise(ren_mask)
    inter = float(np.logical_and(ref_n, ren_n).sum())
    union = float(np.logical_or(ref_n, ren_n).sum())
    iou = inter / union if union > 0 else 0.0
    aspect_err = abs(ren_aspect - ref_aspect) / ref_aspect if ref_aspect > 0 else 1.0
    reliable = (_MIN_FILL < ref_fill < 0.95 and _MIN_FILL < ren_fill < 0.95
                and _bbox_cover(ref_mask) < _MAX_BBOX_COVER
                and _bbox_cover(ren_mask) < _MAX_BBOX_COVER)
    out: dict[str, Any] = {
        "iou": round(iou, 4), "ref_fill": round(ref_fill, 4), "render_fill": round(ren_fill, 4),
        "aspect_ratio_err": round(aspect_err, 4), "ref_aspect": round(ref_aspect, 4),
        "render_aspect": round(ren_aspect, 4), "reliable": reliable,
    }
    if diff_png is not None:
        out["diff_png_path"] = str(_diff_image(ref_n, ren_n, Path(diff_png)))
    return out


def silhouette_aspect(path: Path | str) -> float:
    """Width/height of the foreground bounding box (0.0 when the mask is empty)."""
    mask = foreground_mask(path)
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return 0.0
    h = int(ys.max() - ys.min()) + 1
    w = int(xs.max() - xs.min()) + 1
    return w / h if h else 0.0


CANDIDATE_VIEWS: tuple[str, ...] = (
    "front", "front_right_high", "front_left_high", "right", "left", "back", "back_left_high",
)


CompareFn = Callable[[str, str], dict[str, Any]]


def best_view_match(
    renders: Sequence[RenderView],
    reference: Path | str,
    *,
    candidates: Sequence[str] = CANDIDATE_VIEWS,
    diff_png: Path | str | None = None,
    compare: CompareFn | None = None,
) -> dict[str, Any]:
    """IoU of the render view that best matches ``reference``, and which one it was."""
    compare = compare or compare_silhouette
    per_view: dict[str, float] = {}
    best: tuple[float, RenderView, dict[str, Any]] | None = None
    for view in views_by_preference(renders, candidates) or list(renders):
        if not Path(view.path).is_file():
            continue
        try:
            res = compare(str(view.path), str(reference))
        except (OSError, ValueError):
            continue
        if not isinstance(res, dict) or "iou" not in res:
            continue
        per_view[view.name] = res["iou"]
        if best is None or res["iou"] > best[0]:
            best = (res["iou"], view, res)
    if best is None:
        return {"error": "no comparable render view"}
    _, view, res = best
    out = dict(res)
    out["per_view"] = {k: round(v, 4) for k, v in per_view.items()}
    if diff_png is not None:
        out.update(compare_silhouette(view.path, reference, diff_png=diff_png))
    out["view"] = view.name
    return out


def target_reference(references: Sequence[ReferenceImage]) -> str | None:
    """THE silhouette target."""
    readable = [r for r in references if Path(r.path).is_file()]
    return next((r.path for r in readable if r.role == "target"), readable[0].path if readable else None)


class ReferenceMatch(BaseModel):
    """The one silhouette measurement of a round against its target reference."""

    iou: float
    ref_fill: float | None = None
    render_fill: float | None = None
    aspect_ratio_err: float | None = None
    ref_aspect: float | None = None
    render_aspect: float | None = None
    reliable: bool = Field(default=True, description="both foreground masks localised something")
    view: str = Field(description="the render view that best matches the reference's camera")
    reference: str = Field(description="path of the target reference image")
    conflict: bool = Field(default=False, description="the reference's own proportions contradict the brief's dimensions")
    per_view: dict[str, float] = Field(default_factory=dict)

    @property
    def scorable(self) -> bool:
        """True when the IoU may be read as a shape verdict: the mask is reliable and the reference does not contradict the brief."""
        return self.reliable and not self.conflict


def measure_reference(renders: Sequence[RenderView], spec: Spec, *, compare: CompareFn | None = None,
                      conflict: dict[str, Any] | None = None) -> ReferenceMatch | None:
    """:class:`ReferenceMatch` of ``renders`` against ``spec``'s target reference, or ``None``
    when the spec has no readable reference or no render view could be compared.  ``conflict`` is
    the caller's own :func:`dimension_conflict` of the same reference, when it already has it."""
    from codeverse3d.reference import dimension_conflict  # reference.py imports this module

    ref = target_reference(spec.references)
    if ref is None:
        return None
    res = best_view_match(renders, ref, compare=compare)
    if "iou" not in res:
        return None
    num = {k: float(res[k]) for k in ("ref_fill", "render_fill", "aspect_ratio_err", "ref_aspect", "render_aspect") if isinstance(res.get(k), int | float)}
    return ReferenceMatch(iou=float(res["iou"]), view=str(res.get("view", "")), reference=ref, reliable=bool(res.get("reliable", True)),
                          conflict=bool((conflict if conflict is not None else dimension_conflict(spec, ref)).get("conflict")), per_view=res.get("per_view") or {}, **num)
