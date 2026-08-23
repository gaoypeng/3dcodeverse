"""Contact sheets: a deterministic labelled grid of PNGs (PIL only).

Used for judge inputs and agent observations: one image, every view labelled
under its tile, fixed tile size so the VLM sees consistent scale.
"""

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


def _font(size: int = 15) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("DejaVuSans.ttf", "DejaVuSansMono.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(im: Image.Image, tile: int) -> Image.Image:
    """Letterbox ``im`` into a tile x tile square on the sheet background."""
    im = im.convert("RGBA")
    scale = min(tile / im.width, tile / im.height)
    w, h = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    im = im.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (tile, tile), BG + (255,))
    canvas.alpha_composite(im, ((tile - w) // 2, (tile - h) // 2))
    return canvas.convert("RGB")


def contact_sheet(
    images: Sequence[tuple[str, str | Path]],
    out: Path | str,
    cols: int = 4,
    tile: int = 384,
    label: bool = True,
) -> Path:
    """Write a grid of ``(label, png_path)`` tiles to ``out`` and return it.

    Missing/unreadable images become a grey tile with the label so the sheet
    never silently drops a view.  Output is deterministic for identical inputs.
    """
    if not images:
        raise ValueError("contact_sheet: no images")
    cols = max(1, min(cols, len(images)))
    rows = math.ceil(len(images) / cols)
    cell_h = tile + (LABEL_H if label else 0)
    sheet = Image.new("RGB", (cols * (tile + PAD) + PAD, rows * (cell_h + PAD) + PAD), BG)
    draw = ImageDraw.Draw(sheet)
    font = _font()
    for i, (text, path) in enumerate(images):
        x = PAD + (i % cols) * (tile + PAD)
        y = PAD + (i // cols) * (cell_h + PAD)
        p = Path(path)
        try:
            with Image.open(p) as im:
                tile_im = _fit(im, tile)
        except (OSError, ValueError):
            tile_im = Image.new("RGB", (tile, tile), (200, 200, 200))
            ImageDraw.Draw(tile_im).text((8, 8), "missing image", fill=(90, 0, 0), font=font)
        sheet.paste(tile_im, (x, y))
        if label:
            draw.rectangle([x, y + tile, x + tile - 1, y + tile + LABEL_H - 1], fill=LABEL_BG)
            txt = str(text)
            tw = draw.textlength(txt, font=font)
            while tw > tile - 8 and len(txt) > 4:
                txt = txt[:-2] + "…"
                tw = draw.textlength(txt, font=font)
            draw.text((x + (tile - tw) / 2, y + tile + 6), txt, fill=LABEL_FG, font=font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, format="PNG", optimize=False)
    return out
