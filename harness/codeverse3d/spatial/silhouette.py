"""Silhouette comparison between a render and a reference image.

Foreground mask (documented limits):
* PNG with an alpha channel → ``alpha > 0`` (renders on transparent background).
* Otherwise the background colour is the median of the border pixels; pixels
  farther than ``tol`` from it are "object-coloured", and the background region
  is the flood fill from the image border through non-object pixels.  Enclosed
  pockets therefore count as *foreground* (a chair's back is one silhouette),
  which is what we want for shape comparison but hides interior holes.
* Works for silhouette-mode renders and product photos on a plain backdrop.
  Shaded renders with a contact shadow include the shadow in the mask (pale
  objects on pale studio backdrops are the weak case — always render the
  *render* side in ``silhouette`` mode); textured / gradient backgrounds
  over-segment (``reliable`` becomes False when a fill is < 0.2 % or > 95 %).

IoU is computed on **bbox-normalised** masks at 256² (each silhouette cropped
to its bounding box and scaled to fit, aspect preserved), so framing and scale
differences do not dominate — ``aspect_ratio_err`` carries the shape-proportion
signal separately.
"""

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
#: colour-distance tolerance (0-441) from the border colour: adaptive between these bounds
#: (≈ BORDER_TOL_BASE + BORDER_TOL_K × border std), so flat backdrops get a tight threshold
BORDER_TOL_MIN = 10.0
BORDER_TOL_MAX = 40.0
BORDER_TOL_K = 4.0
_MIN_FILL = 0.002
#: a mask whose BOUNDING BOX spans essentially the whole frame localised nothing, however
#: much or little area it covers.  Measured 2026-08-25 over the 21 reference photos in
#: astra3d-brilliana/references_images: 9 of them (coffee_cup, flower, flower_2, F1 car,
#: blackberry, disney_land, ferris_wheel, micky_character, sc2_protoss) flood-filled to
#: corner-to-corner masks — a coffee cup on a table read as 73.8 % "object" — while the
#: area test passed every one of them, because it only rejects fills above 95 %.  Real
#: single-view renders sit at 0.60-0.69 here and the closest surviving photo at 0.969, so
#: the bar has room on both sides.
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
    """Boolean foreground mask (≤ size², aspect preserved).  See module docstring.

    ``tol`` overrides the adaptive colour tolerance (useful for tests).
    """
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
    """Fraction of the frame the mask's BOUNDING BOX spans (1.0 = corner to corner).

    Separate from fill: a photo whose background the border-colour model failed on comes
    back as a scattered mask of 40-80 % area whose bbox is still the entire image.  Area
    says "plausible object"; the bbox says "nothing was located".
    """
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return 0.0
    h, w = mask.shape
    return float(((ys.max() - ys.min() + 1) / h) * ((xs.max() - xs.min() + 1) / w))


def _normalise(mask: np.ndarray, size: int = MASK_SIZE) -> tuple[np.ndarray, float]:
    """Crop to the mask bbox and scale into a size² canvas; returns (canvas, aspect w/h)."""
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
    """Overlay: red = reference only, blue = render only, grey = both."""
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
    """IoU + fills + aspect error between a render and a reference image.

    Returns ``{iou, ref_fill, render_fill, aspect_ratio_err, ref_aspect, render_aspect,
    reliable, diff_png_path?}``.  ``reliable`` is False when either mask is empty, covers
    more than 95 % of the pixels, or has a bounding box spanning the whole frame — all
    three mean the background estimate failed and the IoU below is arithmetic on noise.
    The caller must honour it: :class:`~codeverse3d.judges.vlm_judge.ReferenceJudge` scores
    ``silhouette_match`` NEUTRAL rather than low, because "we could not measure this" is
    not the same as "it does not match".
    """
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
    """Width/height of the foreground bounding box (0.0 when the mask is empty).

    The one number that says whether a picture and a brief agree about the
    object's PROPORTIONS, independent of framing or resolution.
    """
    mask = foreground_mask(path)
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return 0.0
    h = int(ys.max() - ys.min()) + 1
    w = int(xs.max() - xs.min()) + 1
    return w / h if h else 0.0


#: render views a reference photo could plausibly have been shot from — a straight-on
#: studio elevation matches ``front``/``right``/``left``/``back``, a 3/4 product shot
#: matches the ``*_high`` ring.  ``top``, ``bottom`` and the low ring are never a
#: product-shot camera.  A stored pre-D47 run matches through ``conventions.view_key``.
CANDIDATE_VIEWS: tuple[str, ...] = (
    "front", "front_right_high", "front_left_high", "right", "left", "back", "back_left_high",
)


#: ``compare_silhouette``'s shape: (render png, reference png) → its result dict.  Injectable, so
#: the track's services and the judge's tests can stand in for the mask arithmetic.
CompareFn = Callable[[str, str], dict[str, Any]]


def best_view_match(
    renders: Sequence[RenderView],
    reference: Path | str,
    *,
    candidates: Sequence[str] = CANDIDATE_VIEWS,
    diff_png: Path | str | None = None,
    compare: CompareFn | None = None,
) -> dict[str, Any]:
    """IoU of the render view that best matches ``reference``, and which one it was.

    A reference photograph has ONE camera; comparing it to a fixed ``front`` render
    punishes an object whose reference happens to be a three-quarter shot.  Scoring
    the best-matching candidate view instead measures *shape* rather than *camera
    agreement* — and the chosen view is reported so the number stays auditable.

    Returns the :func:`compare_silhouette` dict plus ``view`` (the winning view name)
    and ``per_view`` (name → IoU).  ``{"error": ...}`` when nothing could be compared.
    """
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
    """THE silhouette target: the first readable reference with role ``target``, else the first
    readable one; ``None`` when no reference file exists."""
    readable = [r for r in references if Path(r.path).is_file()]
    return next((r.path for r in readable if r.role == "target"), readable[0].path if readable else None)


class ReferenceMatch(BaseModel):
    """The one silhouette measurement of a round against its target reference: the
    ``reference_silhouette`` gate records it (and refines on it), the reference judge scores
    ``silhouette_match`` from it.  Until 2026-09-24 the gate compared the ``front`` render only
    while the judge took the best-matching view — 0.26 vs 0.995 on one reference (audit N51)."""

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
        """May the IoU be read as a shape verdict?  Not on an unreliable mask, and not when
        the reference contradicts the brief (the brief wins; the score is neutral)."""
        return self.reliable and not self.conflict


def measure_reference(renders: Sequence[RenderView], spec: Spec, *, compare: CompareFn | None = None) -> ReferenceMatch | None:
    """:class:`ReferenceMatch` of ``renders`` against ``spec``'s target reference, or ``None``
    when the spec has no readable reference or no render view could be compared.  An error in
    ``compare`` itself propagates: the caller decides what "unavailable" means."""
    from codeverse3d.reference import dimension_conflict  # reference.py imports this module

    ref = target_reference(spec.references)
    if ref is None:
        return None
    res = best_view_match(renders, ref, compare=compare)
    if "iou" not in res:
        return None
    num = {k: float(res[k]) for k in ("ref_fill", "render_fill", "aspect_ratio_err", "ref_aspect", "render_aspect") if isinstance(res.get(k), int | float)}
    return ReferenceMatch(iou=float(res["iou"]), view=str(res.get("view", "")), reference=ref, reliable=bool(res.get("reliable", True)),
                          conflict=bool(dimension_conflict(spec, ref).get("conflict")), per_view=res.get("per_view") or {}, **num)
