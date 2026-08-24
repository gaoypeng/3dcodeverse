"""``best_view_match``: score the render view the reference was actually shot from."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from codeverse.contracts.artifacts import RenderView
from codeverse.spatial.silhouette import CANDIDATE_VIEWS, best_view_match


def _shape(path: Path, box, bg=(240, 240, 240), fg=(30, 30, 30)) -> Path:
    im = Image.new("RGB", (256, 256), bg)
    ImageDraw.Draw(im).rectangle(box, fill=fg)
    im.save(path)
    return path


def _views(tmp: Path) -> list[RenderView]:
    tmp.mkdir(parents=True, exist_ok=True)
    boxes = {"front": (40, 40, 90, 220),          # tall and narrow — wrong
             "front_right_34": (60, 60, 200, 200),  # square
             "right": (30, 90, 230, 170)}           # wide and flat — matches the reference
    return [RenderView(name=n, path=str(_shape(tmp / f"{n}.png", b)), width=256, height=256)
            for n, b in boxes.items()]


def test_picks_the_matching_view_not_the_first(tmp_path: Path) -> None:
    ref = _shape(tmp_path / "ref.png", (20, 95, 236, 165))  # wide and flat
    res = best_view_match(_views(tmp_path / "r"), ref)
    assert res["view"] == "right"
    assert res["iou"] == max(res["per_view"].values())
    assert set(res["per_view"]) == {"front", "front_right_34", "right"}
    # the fixed-front comparison would have scored much worse
    assert res["iou"] > res["per_view"]["front"] + 0.2


def test_candidate_filter_excludes_top_and_low_cameras(tmp_path: Path) -> None:
    assert "top" not in CANDIDATE_VIEWS and "low_front_left" not in CANDIDATE_VIEWS
    views = _views(tmp_path / "r")
    views.append(RenderView(name="top", path=views[0].path, width=256, height=256))
    res = best_view_match(views, _shape(tmp_path / "ref.png", (20, 95, 236, 165)))
    assert "top" not in res["per_view"]


def test_diff_png_and_empty_input(tmp_path: Path) -> None:
    ref = _shape(tmp_path / "ref.png", (20, 95, 236, 165))
    res = best_view_match(_views(tmp_path / "r"), ref, diff_png=tmp_path / "d.png")
    assert (tmp_path / "d.png").is_file() and res["view"] == "right"
    assert "error" in best_view_match([], ref)
