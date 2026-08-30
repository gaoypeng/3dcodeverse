from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from codeverse.spatial.silhouette import compare_silhouette, foreground_mask


def _disc(path: Path, size: tuple[int, int], box: tuple[int, int, int, int], bg=(240, 240, 240), fg=(30, 30, 30)) -> Path:
    im = Image.new("RGB", size, bg)
    ImageDraw.Draw(im).ellipse(box, fill=fg)
    im.save(path)
    return path


def test_same_shape_different_framing(tmp_path: Path) -> None:
    a = _disc(tmp_path / "a.png", (400, 300), (100, 50, 300, 250))
    b = _disc(tmp_path / "b.png", (512, 512), (56, 56, 456, 456), fg=(80, 20, 20))
    r = compare_silhouette(b, a, diff_png=tmp_path / "diff.png")
    assert r["iou"] > 0.95 and r["aspect_ratio_err"] < 0.02 and r["reliable"]
    assert (tmp_path / "diff.png").is_file()


def test_different_aspect(tmp_path: Path) -> None:
    a = _disc(tmp_path / "a.png", (400, 400), (100, 100, 300, 300))
    b = _disc(tmp_path / "b.png", (400, 400), (50, 150, 350, 250))  # wide ellipse
    r = compare_silhouette(b, a)
    assert r["aspect_ratio_err"] > 1.0 and r["iou"] < 0.8


def test_alpha_mask_and_enclosed_pockets(tmp_path: Path) -> None:
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 180, 180], fill=(200, 200, 200, 255))
    d.rectangle([80, 80, 120, 120], fill=(0, 0, 0, 0))  # a hole
    p = tmp_path / "alpha.png"
    im.save(p)
    m = foreground_mask(p)
    assert m.sum() == pytest.approx(161 * 161 - 41 * 41, rel=0.01)
    # opaque version: the pocket is enclosed → counted as foreground
    im2 = Image.new("RGB", (200, 200), (255, 255, 255))
    d2 = ImageDraw.Draw(im2)
    d2.rectangle([20, 20, 180, 180], fill=(40, 40, 40))
    d2.rectangle([80, 80, 120, 120], fill=(255, 255, 255))
    p2 = tmp_path / "opaque.png"
    im2.save(p2)
    assert foreground_mask(p2).sum() == pytest.approx(161 * 161, rel=0.01)


def test_unreliable_when_empty(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    Image.new("RGB", (100, 100), (250, 250, 250)).save(blank)
    a = _disc(tmp_path / "a.png", (100, 100), (20, 20, 80, 80))
    r = compare_silhouette(blank, a)
    assert r["iou"] == 0.0 and not r["reliable"]


def test_a_photo_the_background_model_failed_on_is_unreliable(tmp_path: Path) -> None:
    """A corner-to-corner mask is failed background segmentation, not a silhouette."""
    import numpy as np
    from PIL import Image

    from codeverse.spatial.silhouette import _bbox_cover

    rng = np.random.default_rng(0)
    busy = tmp_path / "busy.jpg"
    Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)).save(busy)
    clean = _disc(tmp_path / "clean.png", (400, 300), (150, 90, 250, 210))

    assert _bbox_cover(foreground_mask(busy)) > 0.98, "a noise field localises nothing"
    assert _bbox_cover(foreground_mask(clean)) < 0.98, "a disc on a flat backdrop does"
    assert compare_silhouette(clean, busy)["reliable"] is False
    assert compare_silhouette(clean, clean)["reliable"] is True


def test_a_render_that_fills_its_frame_is_still_reliable(tmp_path: Path) -> None:
    """The bar must not condemn the render side. Real single-view renders measured at
    bbox coverage 0.60-0.69; a tight crop must still pass."""
    from codeverse.spatial.silhouette import _bbox_cover

    tight = _disc(tmp_path / "tight.png", (300, 300), (12, 12, 288, 288))
    assert 0.80 < _bbox_cover(foreground_mask(tight)) < 0.98
    assert compare_silhouette(tight, tight)["reliable"] is True
