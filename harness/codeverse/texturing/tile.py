"""Tileability: seam scoring, mirror-blend seam fixing, resizing and saving.

* ``seam_score(img)``: mean absolute difference (0..1) between the left/right and
  top/bottom wrap edges, normalised by the image's own local contrast so a noisy
  texture is not penalised for being noisy.  ≈0 = perfect wrap.
* ``make_tileable(img, border_frac)``: cross-fades each edge band with the mirrored
  opposite band (a smoothstep ramp over ``border_frac`` of the width) so the wrap
  seam disappears; content in the middle is untouched.
* ``offset_check(img)``: half-offset the image (seams move to the centre) — the
  classic visual check; returns the offset image for inspection.
* ``fit_size`` / ``save_texture``: power-of-two downscale + PNG or JPEG q90.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

#: seam score above which a texture is considered NOT tileable (after make_tileable)
SEAM_MAX = 0.08


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0


def seam_score(img: Image.Image | np.ndarray) -> float:
    """Mean |Δ| across the wrap seams relative to the mean |Δ| between neighbouring
    pixel columns/rows inside the image (so 1.0 ≈ 'the seam is as strong as the
    texture's own detail'); clamped to [0, 1]."""
    a = _arr(img) if isinstance(img, Image.Image) else np.asarray(img, dtype=np.float32)
    if a.ndim == 2:
        a = a[:, :, None]
    h, w = a.shape[:2]
    if h < 4 or w < 4:
        return 0.0
    seam_lr = np.abs(a[:, 0, :] - a[:, -1, :]).mean()
    seam_tb = np.abs(a[0, :, :] - a[-1, :, :]).mean()
    # typical one-step difference inside the image (excluding the seam)
    inner_lr = np.abs(a[:, 1:, :] - a[:, :-1, :]).mean()
    inner_tb = np.abs(a[1:, :, :] - a[:-1, :, :]).mean()
    # a seam is bad when it exceeds the local detail noticeably; blend absolute + relative
    rel = 0.5 * (seam_lr / max(inner_lr, 1e-3) + seam_tb / max(inner_tb, 1e-3))
    absolute = 0.5 * (seam_lr + seam_tb)
    score = 0.5 * absolute + 0.5 * min(1.0, max(0.0, (rel - 1.0) / 8.0))
    return float(min(1.0, max(0.0, score)))


def _ramp(n: int) -> np.ndarray:
    t = (np.arange(n, dtype=np.float32) + 0.5) / n
    return t * t * (3.0 - 2.0 * t)  # smoothstep 0 → 1


def make_tileable(img: Image.Image, border_frac: float = 0.12) -> Image.Image:
    """Mirror-blend the wrap seams.  Along X: the left band is cross-faded with the
    mirrored right band and vice versa (weights symmetric so the wrap is exact);
    then the same along Y.  ``border_frac`` ∈ [0.05, 0.3] of the size."""
    border_frac = float(min(0.3, max(0.05, border_frac)))
    a = _arr(img)
    h, w = a.shape[:2]
    bw, bh = max(2, int(round(w * border_frac))), max(2, int(round(h * border_frac)))
    out = a.copy()
    # horizontal: blend left band with mirrored right band
    r = _ramp(bw)[None, :, None]  # 0 at the outer edge → 1 inside
    left, right = a[:, :bw, :], a[:, w - bw:, :]
    mir_right = right[:, ::-1, :]  # right band mirrored so its outer edge meets the left edge
    mir_left = left[:, ::-1, :]
    out[:, :bw, :] = left * (0.5 + 0.5 * r) + mir_right * (0.5 - 0.5 * r)
    out[:, w - bw:, :] = right * (0.5 + 0.5 * r[:, ::-1, :]) + mir_left * (0.5 - 0.5 * r[:, ::-1, :])
    a = out.copy()
    r = _ramp(bh)[:, None, None]
    top, bottom = a[:bh, :, :], a[h - bh:, :, :]
    mir_bottom, mir_top = bottom[::-1, :, :], top[::-1, :, :]
    out[:bh, :, :] = top * (0.5 + 0.5 * r) + mir_bottom * (0.5 - 0.5 * r)
    out[h - bh:, :, :] = bottom * (0.5 + 0.5 * r[::-1, :, :]) + mir_top * (0.5 - 0.5 * r[::-1, :, :])
    return Image.fromarray((np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")


def offset_check(img: Image.Image) -> Image.Image:
    """Roll the image by half its size so the wrap seams sit in the centre."""
    a = _arr(img)
    h, w = a.shape[:2]
    rolled = np.roll(np.roll(a, w // 2, axis=1), h // 2, axis=0)
    return Image.fromarray((rolled * 255.0 + 0.5).astype(np.uint8), "RGB")


def fit_size(img: Image.Image, size: int) -> Image.Image:
    """Square, power-of-two-ish downscale to ``size`` (never upscales)."""
    w, h = img.size
    if w != h:
        s = min(w, h)
        img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    if img.size[0] > size:
        img = img.resize((size, size), Image.LANCZOS)
    return img.convert("RGB")


def save_texture(img: Image.Image, path: Path, *, fmt: str | None = None, quality: int = 90) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or ("JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG")).upper()
    if fmt == "JPEG":
        img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)
    else:
        img.convert("RGB").save(path, "PNG", optimize=True)
    return path


def tile_preview(img: Image.Image, reps: int = 2, size: int = 512) -> Image.Image:
    """``reps × reps`` repetition for eyeballing tileability."""
    t = img.convert("RGB").resize((size // reps, size // reps), Image.LANCZOS)
    out = Image.new("RGB", (t.size[0] * reps, t.size[1] * reps))
    for i in range(reps):
        for j in range(reps):
            out.paste(t, (i * t.size[0], j * t.size[1]))
    return out
