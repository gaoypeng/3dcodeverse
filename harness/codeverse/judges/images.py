"""Judge image preparation: downscale, label strips, caching, view geometry.

Every image sent to a judge goes through ``prepare_image`` so the token cost is
bounded (≤ ``max_px`` on the long side) and every view carries a burnt-in label
(``VIEW 3/8 — front · az 0° el 8°``) that the model can cite as evidence.
Prepared files are cached under ``<cache_dir>/<sha>.png`` keyed by source path,
mtime, size, max_px and label text.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from codeverse.config import get_settings
from codeverse.contracts.artifacts import RenderView
from codeverse.contracts.chat import ImagePart
from codeverse.conventions import OBJECT_VIEWS, SCENE_VIEWS, ViewPreset

_PRESETS: dict[str, ViewPreset] = {v.name: v for v in (*OBJECT_VIEWS, *SCENE_VIEWS)}


class JudgeImageError(FileNotFoundError):
    """A render referenced by a RenderSet is missing or unreadable."""


def default_cache_dir() -> Path:
    return get_settings().cache_dir / "judge_images"


def view_az_el(view: RenderView) -> tuple[float, float] | None:
    """Azimuth/elevation (deg) of a view: from the preset name, else from camera geometry (Y-up)."""
    if view.name in _PRESETS:
        p = _PRESETS[view.name]
        return p.azimuth_deg, p.elevation_deg
    if view.camera_position is not None:
        cx, cy, cz = view.camera_position
        lx, ly, lz = view.look_at or (0.0, 0.0, 0.0)
        dx, dy, dz = cx - lx, cy - ly, cz - lz
        horiz = math.hypot(dx, dz)
        if horiz < 1e-9 and abs(dy) < 1e-9:
            return None
        az = math.degrees(math.atan2(dx, dz)) % 360.0  # 0 = +Z front, CCW from above
        el = math.degrees(math.atan2(dy, horiz))
        return round(az, 1), round(el, 1)
    return None


def view_label(view: RenderView, index: int, total: int) -> str:
    """``VIEW 3/8 — front · az 0° el 8°`` (+ ``t=1.5s`` / mode when relevant)."""
    bits = [f"VIEW {index}/{total} — {view.name}"]
    azel = view_az_el(view)
    if azel is not None:
        bits.append(f"az {azel[0]:.0f}° el {azel[1]:.0f}°")
    if view.mode and view.mode != "shaded":
        bits.append(view.mode)
    if view.time_s is not None:
        bits.append(f"t={view.time_s:g}s")
    return " · ".join(bits)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for cand in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cache_key(src: Path, max_px: int, label: str) -> str:
    st = src.stat()
    h = hashlib.sha1(f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|{max_px}|{label}".encode())
    return h.hexdigest()[:20]


def prepare_image(
    src: str | Path, *, label: str = "", max_px: int = 768, cache_dir: Path | None = None
) -> Path:
    """Downscale ``src`` to ≤ ``max_px`` on its long side and burn ``label`` into a top strip.

    Returns the cached PNG path.  Raises ``JudgeImageError`` if the source is missing.
    """
    src = Path(src)
    if not src.is_file():
        raise JudgeImageError(f"judge image missing: {src}")
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{_cache_key(src, max_px, label)}.png"
    if out.is_file():
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_px / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        if label:
            im = _with_label_strip(im, label)
        tmp = out.with_suffix(".tmp.png")
        im.save(tmp, format="PNG", optimize=True)
        tmp.replace(out)
    return out


def _with_label_strip(im: Image.Image, label: str) -> Image.Image:
    w, h = im.size
    strip_h = max(22, int(h * 0.055))
    font = _font(max(12, int(strip_h * 0.62)))
    canvas = Image.new("RGB", (w, h + strip_h), (18, 18, 22))
    canvas.paste(im, (0, strip_h))
    draw = ImageDraw.Draw(canvas)
    text = label
    # truncate to fit
    while text and draw.textlength(text, font=font) > w - 12 and len(text) > 8:
        text = text[:-2]
    font_px = getattr(font, "size", 11)
    draw.text((6, max(2, (strip_h - font_px) // 2)), text, fill=(245, 245, 240), font=font)
    return canvas


def image_part(path: Path, label: str) -> ImagePart:
    return ImagePart(path=str(path), mime="image/png", label=label)
