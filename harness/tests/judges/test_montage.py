from pathlib import Path

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.judges.prompt_builder import (
    plan_montages,
)
from tests.judges.conftest import draw_chair

#: the 14-view rig in OBJECT_VIEWS order (D47)
OBJ = ("front_right_high", "back_right_high", "back_left_high", "front_left_high",
       "front", "right", "back", "left",
       "front_right_low", "back_right_low", "back_left_low", "front_left_low",
       "top", "bottom")
#: the clay rig's own cameras (OBJECT_CLAY_VIEWS)
CLAY = ("front_right_34", "back_left_34", "top", "low_front_left")


def _views(tmp: Path, names, *, mode="shaded", prefix="v"):
    out = []
    for n in names:
        p = draw_chair(tmp / f"{prefix}_{n}.png")
        out.append(RenderView(name=n, path=str(p), mode=mode))
    return out


def test_plan_montages_articulated_and_geometry(tmp_path):
    poses = _views(tmp_path, ["pose_rest", "pose_J@upper", "pose_K@upper"], prefix="p")
    sheet = _views(tmp_path, ["articulation_sheet"], prefix="s")
    rs = RenderSet(views=_views(tmp_path, OBJ) + sheet + poses)
    clay = RenderSet(views=_views(tmp_path, ["front_right_34", "top", "back"], mode="clay", prefix="c"))
    ms = plan_montages(rs, geometry_views=clay, max_montages=3, detail_crops=0)
    assert [m.kind for m in ms] == ["shaded", "pose_sheet", "geometry"]
    assert ms[1].passthrough and ms[1].tiles[0].name == "articulation_sheet"
    # geometry tiles are matched to the primary montage's views first ("top" is in montage 1)
    assert [v.name for v in ms[2].tiles] == ["top", "front_right_34", "back"]
    assert "GEOMETRY-ONLY views (clay" in ms[2].title
    # more room: secondary shaded then the pose 2×2 (duplicate of the sheet, lowest priority)
    ms5 = plan_montages(rs, geometry_views=clay, max_montages=7, detail_crops=0)
    assert [m.kind for m in ms5] == ["shaded", "pose_sheet", "geometry", "shaded", "shaded", "shaded", "poses"]
