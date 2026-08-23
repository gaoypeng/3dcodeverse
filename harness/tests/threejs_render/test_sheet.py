"""contact_sheet: layout, labels, missing tiles, determinism."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from codeverse.spatial.sheet import LABEL_H, PAD, contact_sheet


def _png(path: Path, color, size=(120, 80)):
    Image.new("RGB", size, color).save(path)
    return path


def test_sheet_layout_and_labels(tmp_path: Path):
    imgs = [(f"v{i}", _png(tmp_path / f"{i}.png", (i * 40, 100, 200))) for i in range(5)]
    out = contact_sheet(imgs, tmp_path / "sheet.png", cols=3, tile=100)
    with Image.open(out) as im:
        assert im.size == (3 * (100 + PAD) + PAD, 2 * (100 + LABEL_H + PAD) + PAD)
        # label strip under the first tile is dark
        assert im.getpixel((PAD + 2, PAD + 100 + 2))[0] < 60
        # the first tile centre carries the first image colour (letterboxed)
        assert im.getpixel((PAD + 50, PAD + 50)) == (0, 100, 200)


def test_sheet_missing_image_is_grey_tile(tmp_path: Path):
    out = contact_sheet([("ok", _png(tmp_path / "a.png", (10, 200, 10))), ("gone", tmp_path / "missing.png")], tmp_path / "s.png", cols=2, tile=64)
    with Image.open(out) as im:
        assert im.getpixel((PAD + 64 + PAD + 40, PAD + 40)) == (200, 200, 200)


def test_sheet_deterministic(tmp_path: Path):
    imgs = [("a", _png(tmp_path / "a.png", (1, 2, 3))), ("b", _png(tmp_path / "b.png", (3, 2, 1)))]
    a = contact_sheet(imgs, tmp_path / "s1.png", cols=2, tile=64).read_bytes()
    b = contact_sheet(imgs, tmp_path / "s2.png", cols=2, tile=64).read_bytes()
    assert a == b


def test_sheet_no_label(tmp_path: Path):
    out = contact_sheet([("a", _png(tmp_path / "a.png", (1, 2, 3)))], tmp_path / "s.png", cols=4, tile=50, label=False)
    with Image.open(out) as im:
        assert im.size == (50 + 2 * PAD, 50 + 2 * PAD)


def test_sheet_empty_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        contact_sheet([], tmp_path / "s.png")
