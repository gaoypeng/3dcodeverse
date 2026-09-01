"""tile.py: seam score, mirror-blend tileability, sizing."""

from __future__ import annotations

import numpy as np
from PIL import Image

from codeverse.texturing.generate import (
    SEAM_MAX,
    fit_size,
    make_tileable,
    save_texture,
    seam_score,
)
from tests.texturing.conftest import procedural_texture


def _tileable(size: int = 64) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size] / size
    a = 0.5 + 0.4 * np.sin(2 * np.pi * 3 * x) * np.cos(2 * np.pi * 2 * y)
    return Image.fromarray((np.stack([a, a, a], 2) * 255).astype(np.uint8), "RGB")


def test_seam_score_tileable_is_low_and_gradient_is_high():
    assert seam_score(_tileable()) < 0.02
    bad = procedural_texture("x", size=64)  # carries a horizontal gradient → seam
    assert seam_score(bad) > seam_score(_tileable())


def test_make_tileable_makes_wrap_edges_equal_and_lowers_score():
    bad = procedural_texture("wood", size=128)
    before = seam_score(bad)
    fixed = make_tileable(bad, 0.12)
    after = seam_score(fixed)
    assert after < before and after <= SEAM_MAX
    a = np.asarray(fixed).astype(int)
    assert np.abs(a[:, 0, :] - a[:, -1, :]).mean() < 2.0  # wrap-exact horizontally
    assert np.abs(a[0, :, :] - a[-1, :, :]).mean() < 2.0
    # centre untouched
    b = np.asarray(bad).astype(int)
    assert np.array_equal(a[40:88, 40:88], b[40:88, 40:88])


def test_fit_size_save_and_helpers(tmp_path):
    img = Image.new("RGB", (300, 200), (10, 20, 30))
    sq = fit_size(img, 128)
    assert sq.size == (128, 128)
    assert fit_size(Image.new("RGB", (64, 64)), 128).size == (64, 64)  # never upscales
    p = save_texture(sq, tmp_path / "t.jpg")
    assert p.is_file() and Image.open(p).format == "JPEG"
    p2 = save_texture(sq, tmp_path / "t.png")
    assert Image.open(p2).format == "PNG"
