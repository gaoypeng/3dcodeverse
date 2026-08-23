from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from codeverse.contracts.artifacts import RenderView
from codeverse.spatial.silhouette import compare_silhouette, foreground_mask, silhouette_series


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


def test_series_picks_best_view(tmp_path: Path) -> None:
    ref = _disc(tmp_path / "ref.png", (300, 300), (50, 50, 250, 250))
    good = _disc(tmp_path / "good.png", (300, 300), (40, 40, 260, 260))
    bad = _disc(tmp_path / "bad.png", (300, 300), (20, 120, 280, 180))
    views = [RenderView(name="front", path=str(bad)), RenderView(name="top", path=str(good))]
    res = silhouette_series(views, [ref], diff_dir=tmp_path)
    assert res[0]["best_view"] == "top" and res[0]["iou"] > 0.9
    assert Path(res[0]["diff_png_path"]).is_file()
