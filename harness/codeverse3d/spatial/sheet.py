"""Contact sheets: a deterministic labelled grid of PNGs (PIL only)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LABEL_H = 28
PAD = 6
BG = (250, 250, 250)
LABEL_BG = (34, 34, 38)
LABEL_FG = (240, 240, 240)

_FONTS = ("DejaVuSans.ttf", "DejaVuSansMono.ttf", "LiberationSans-Regular.ttf", "Arial.ttf")
_FONTS_BOLD = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arialbd.ttf")


def load_font(size: int = 15, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """First available truetype font at ``size`` (PIL default as last resort)."""
    names = (*_FONTS_BOLD, *_FONTS) if bold else _FONTS
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(im: Image.Image, tile_w: int, tile_h: int) -> Image.Image:
    """Letterbox ``im`` into a tile_w x tile_h cell on the sheet background."""
    im = im.convert("RGBA")
    scale = min(tile_w / im.width, tile_h / im.height)
    w, h = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    im = im.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (tile_w, tile_h), BG + (255,))
    canvas.alpha_composite(im, ((tile_w - w) // 2, (tile_h - h) // 2))
    return canvas.convert("RGB")


def tile_size(tile: int | tuple[int, int], sample: str | Path | None = None) -> tuple[int, int]:
    """Resolve the ``tile`` argument."""
    if isinstance(tile, tuple):
        return max(1, int(tile[0])), max(1, int(tile[1]))
    tile_w = max(1, int(tile))
    if sample is None:
        return tile_w, tile_w
    try:
        with Image.open(sample) as im:
            return tile_w, max(1, round(tile_w * im.height / im.width))
    except (OSError, ValueError):
        return tile_w, tile_w


def contact_sheet(
    images: Sequence[tuple[str, str | Path]],
    out: Path | str,
    cols: int = 4,
    tile: int | tuple[int, int] = 384,
    label: bool = True,
) -> Path:
    """Write a grid of ``(label, png_path)`` tiles to ``out`` and return it."""
    if not images:
        raise ValueError("contact_sheet: no images")
    tile_w, tile_h = tile_size(tile)
    cols = max(1, min(cols, len(images)))
    rows = math.ceil(len(images) / cols)
    cell_h = tile_h + (LABEL_H if label else 0)
    sheet = Image.new("RGB", (cols * (tile_w + PAD) + PAD, rows * (cell_h + PAD) + PAD), BG)
    draw = ImageDraw.Draw(sheet)
    font = load_font()
    for i, (text, path) in enumerate(images):
        x = PAD + (i % cols) * (tile_w + PAD)
        y = PAD + (i // cols) * (cell_h + PAD)
        p = Path(path)
        try:
            with Image.open(p) as im:
                tile_im = _fit(im, tile_w, tile_h)
        except (OSError, ValueError):
            tile_im = Image.new("RGB", (tile_w, tile_h), (200, 200, 200))
            ImageDraw.Draw(tile_im).text((8, 8), "missing image", fill=(90, 0, 0), font=font)
        sheet.paste(tile_im, (x, y))
        if label:
            draw.rectangle([x, y + tile_h, x + tile_w - 1, y + tile_h + LABEL_H - 1], fill=LABEL_BG)
            txt = str(text)
            tw = draw.textlength(txt, font=font)
            while tw > tile_w - 8 and len(txt) > 4:
                txt = txt[:-2] + "…"
                tw = draw.textlength(txt, font=font)
            draw.text((x + (tile_w - tw) / 2, y + tile_h + 6), txt, fill=LABEL_FG, font=font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, format="PNG", optimize=False)
    return out


def write_gif(frames: Sequence[str | Path], out: Path | str, *, fps: int = 12, width: int | None = None) -> Path:
    """Animated GIF from ``frames`` (adaptive 128-colour palette, looping)."""
    if not frames:
        raise ValueError("write_gif: no frames")
    ims = []
    for f in frames:
        im = Image.open(f).convert("RGB")
        if width and im.width != width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.BILINEAR)
        ims.append(im.convert("P", palette=Image.ADAPTIVE, colors=128))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ims[0].save(out, save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0, optimize=False)
    return out


# --------------------------------------------------------------------------- montage helpers (judge inputs)
MONTAGE_MAX_TILES = 4


def montage_2x2(
    images: Sequence[tuple[str, str | Path]],
    out: Path | str,
    tile: int = 512,
) -> Path:
    """A labelled ≤ 2×2 grid (1–4 tiles) — the judge-facing montage format."""
    if not 1 <= len(images) <= MONTAGE_MAX_TILES:
        raise ValueError(f"montage_2x2: need 1..{MONTAGE_MAX_TILES} tiles, got {len(images)}")
    cols = 1 if len(images) == 1 else 2
    return contact_sheet(images, out, cols=cols, tile=tile, label=True)


def crop_region(
    src: Path | str,
    out: Path | str,
    box_frac: tuple[float, float, float, float],
    *,
    min_px: int = 512,
) -> Path:
    """Crop ``src`` to the fractional box ``(left, top, right, bottom)`` (0..1) and upscale so
    the long side is ≥ ``min_px`` (a "detail crop" for judges)."""
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box_frac)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"crop_region: empty box {box_frac}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        region = im.crop((round(x0 * w), round(y0 * h), max(round(x0 * w) + 1, round(x1 * w)), max(round(y0 * h) + 1, round(y1 * h))))
        rw, rh = region.size
        scale = max(1.0, min_px / max(rw, rh))
        if scale > 1.0:
            region = region.resize((max(1, round(rw * scale)), max(1, round(rh * scale))), Image.LANCZOS)
        region.save(out, format="PNG", optimize=False)
    return out
