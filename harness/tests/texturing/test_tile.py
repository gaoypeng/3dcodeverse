"""tile.py: sizing and saving (the seam score is gated in test_generate)."""

from __future__ import annotations

from PIL import Image

from codeverse3d.texturing.generate import fit_size, save_texture


def test_fit_size_save_and_helpers(tmp_path):
    img = Image.new("RGB", (300, 200), (10, 20, 30))
    sq = fit_size(img, 128)
    assert sq.size == (128, 128)
    assert fit_size(Image.new("RGB", (64, 64)), 128).size == (64, 64)  # never upscales
    p = save_texture(sq, tmp_path / "t.jpg")
    assert p.is_file() and Image.open(p).format == "JPEG"
    p2 = save_texture(sq, tmp_path / "t.png")
    assert Image.open(p2).format == "PNG"
