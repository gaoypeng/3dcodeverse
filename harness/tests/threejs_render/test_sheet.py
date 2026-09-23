"""contact_sheet: layout, labels, missing tiles, determinism."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.spatial.sheet import LABEL_H, PAD, contact_sheet


def _png(path: Path, color, size=(120, 80)):
    Image.new("RGB", size, color).save(path)
    return path


def test_sheet_layout_labels_missing_tile_and_empty(tmp_path: Path):
    imgs = [(f"v{i}", _png(tmp_path / f"{i}.png", (i * 40, 100, 200))) for i in range(5)]
    out = contact_sheet(imgs, tmp_path / "sheet.png", cols=3, tile=100)
    with Image.open(out) as im:
        assert im.size == (3 * (100 + PAD) + PAD, 2 * (100 + LABEL_H + PAD) + PAD)
        # label strip under the first tile is dark
        assert im.getpixel((PAD + 2, PAD + 100 + 2))[0] < 60
        # the first tile centre carries the first image colour (letterboxed)
        assert im.getpixel((PAD + 50, PAD + 50)) == (0, 100, 200)
    # a missing image is a grey tile, not a crash
    out = contact_sheet([("ok", _png(tmp_path / "a.png", (10, 200, 10))), ("gone", tmp_path / "missing.png")], tmp_path / "s.png", cols=2, tile=64)
    with Image.open(out) as im:
        assert im.getpixel((PAD + 64 + PAD + 40, PAD + 40)) == (200, 200, 200)
    with pytest.raises(ValueError):
        contact_sheet([], tmp_path / "s.png")
