from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from codeverse3d.spatial.silhouette import compare_silhouette, foreground_mask


def _disc(path: Path, size: tuple[int, int], box: tuple[int, int, int, int], bg=(240, 240, 240), fg=(30, 30, 30)) -> Path:
    im = Image.new("RGB", size, bg)
    ImageDraw.Draw(im).ellipse(box, fill=fg)
    im.save(path)
    return path


def test_iou_ignores_framing_but_not_aspect(tmp_path: Path) -> None:
    a = _disc(tmp_path / "a.png", (400, 300), (100, 50, 300, 250))
    b = _disc(tmp_path / "b.png", (512, 512), (56, 56, 456, 456), fg=(80, 20, 20))
    r = compare_silhouette(b, a, diff_png=tmp_path / "diff.png")
    assert r["iou"] > 0.95 and r["aspect_ratio_err"] < 0.02 and r["reliable"]
    assert (tmp_path / "diff.png").is_file()
    wide = _disc(tmp_path / "w.png", (400, 400), (50, 150, 350, 250))
    r = compare_silhouette(wide, _disc(tmp_path / "c.png", (400, 400), (100, 100, 300, 300)))
    assert r["aspect_ratio_err"] > 1.0 and r["iou"] < 0.8


def test_a_mask_that_fills_its_frame_is_failed_segmentation_but_a_tight_render_is_not(tmp_path: Path) -> None:
    """Bbox cover > 0.98 is failed background segmentation; a tightly cropped render (~0.9) still passes."""
    import numpy as np

    from codeverse3d.spatial.silhouette import _bbox_cover

    busy = tmp_path / "busy.jpg"
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 400, 3), dtype=np.uint8)).save(busy)
    clean = _disc(tmp_path / "clean.png", (400, 300), (150, 90, 250, 210))
    assert _bbox_cover(foreground_mask(busy)) > 0.98
    assert compare_silhouette(clean, busy)["reliable"] is False
    tight = _disc(tmp_path / "tight.png", (300, 300), (12, 12, 288, 288))
    assert 0.80 < _bbox_cover(foreground_mask(tight)) < 0.98
    assert compare_silhouette(tight, tight)["reliable"] is True
