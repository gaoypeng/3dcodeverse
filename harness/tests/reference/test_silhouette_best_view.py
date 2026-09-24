"""``best_view_match``: score the render view the reference was actually shot from."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from codeverse3d.contracts.artifacts import RenderView
from codeverse3d.spatial.silhouette import CANDIDATE_VIEWS, best_view_match


def _shape(path: Path, box, bg=(240, 240, 240), fg=(30, 30, 30)) -> Path:
    im = Image.new("RGB", (256, 256), bg)
    ImageDraw.Draw(im).rectangle(box, fill=fg)
    im.save(path)
    return path


def _views(tmp: Path) -> list[RenderView]:
    tmp.mkdir(parents=True, exist_ok=True)
    boxes = {"front": (40, 40, 90, 220),          # tall and narrow — wrong
             "front_right_high": (60, 60, 200, 200),  # square
             "right": (30, 90, 230, 170)}           # wide and flat — matches the reference
    return [RenderView(name=n, path=str(_shape(tmp / f"{n}.png", b)), width=256, height=256)
            for n, b in boxes.items()]


def test_best_view_skips_top_and_low_cameras_and_writes_the_diff(tmp_path: Path) -> None:
    assert "top" not in CANDIDATE_VIEWS and "bottom" not in CANDIDATE_VIEWS
    assert "front_right_low" not in CANDIDATE_VIEWS
    views = _views(tmp_path / "r")
    views.append(RenderView(name="top", path=views[0].path, width=256, height=256))
    ref = _shape(tmp_path / "ref.png", (20, 95, 236, 165))
    res = best_view_match(views, ref, diff_png=tmp_path / "d.png")
    assert "top" not in res["per_view"]
    assert (tmp_path / "d.png").is_file() and res["view"] == "right"
    assert "error" in best_view_match([], ref)


def test_a_pre_d47_run_matches_its_own_cameras_through_one_alias_map(tmp_path: Path) -> None:
    """N62: four lists each kept a different subset of the old ``*_34`` names; now
    ``conventions.LEGACY_VIEW_ALIASES`` maps them, and the old low camera stays out as its twin does."""
    views = _views(tmp_path / "r")
    old = [RenderView(name=n, path=views[2].path, width=256, height=256) for n in ("front_right_34", "low_front_left")]
    res = best_view_match(old, _shape(tmp_path / "ref.png", (20, 95, 236, 165)))
    assert res["view"] == "front_right_34" and set(res["per_view"]) == {"front_right_34"}
