"""Compose a contact-sheet montage of a source's project thumbnails — a quick visual
overview for review (adapted from data_pipeline_operators/viz_factory.py's grid step).
Pillow only; reuses each project's existing renders."""

from __future__ import annotations

import math
from pathlib import Path

IMG_EXT = (".png", ".webp", ".jpg", ".jpeg")


def _thumb(project_dir: Path):
    from PIL import Image
    r = project_dir / "renders"
    cands = []
    if r.is_dir():
        cands.append(r / "thumb.png")
        cands += sorted(r.glob("*"))
    cands += sorted(project_dir.glob("*"))
    for c in cands:
        if c.is_file() and c.suffix.lower() in IMG_EXT:
            try:
                return Image.open(c)
            except Exception:
                continue
    return None


def make_grid(project_dirs, out_path: Path, cols: int = 6, cell: int = 180) -> int:
    """Montage each project's thumbnail into a labelled grid PNG. Returns #cells drawn."""
    from PIL import Image, ImageDraw
    items = [(d.name, t) for d in project_dirs if (t := _thumb(d)) is not None]
    if not items:
        return 0
    pad, lbl = 6, 16
    rows = math.ceil(len(items) / cols)
    W = cols * (cell + pad) + pad
    H = rows * (cell + lbl + pad) + pad
    sheet = Image.new("RGB", (W, H), (245, 245, 247))
    draw = ImageDraw.Draw(sheet)
    for i, (name, img) in enumerate(items):
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), pad + r * (cell + lbl + pad)
        thumb = img.convert("RGBA")
        thumb.thumbnail((cell, cell))
        bg = Image.new("RGBA", (cell, cell), (255, 255, 255, 255))
        bg.alpha_composite(thumb, ((cell - thumb.width) // 2, (cell - thumb.height) // 2))
        sheet.paste(bg.convert("RGB"), (x, y))
        draw.text((x + 2, y + cell + 2), name[:30], fill=(90, 90, 90))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return len(items)
