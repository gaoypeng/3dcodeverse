from pathlib import Path

import pytest
from PIL import Image

from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.judges.prompt_builder import (
    JudgeImageError,
    describe_montages,
    montage_label,
    montage_strip,
    plan_montages,
    rank_views,
    render_montage,
    shuffle_montages,
)
from codeverse.spatial.sheet import crop_region, montage_2x2
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


def test_rank_views_objects_and_scenes():
    vs = [RenderView(name=n, path="x") for n in ("left", "top", "zzz", "front_right_high")]
    assert [v.name for v in rank_views(vs, scene=False)] == ["front_right_high", "top", "left", "zzz"]
    # scene rig: the scene's OWN cameras (any name that is not a harness rig name) come
    # first — they are the pictures being graded; the overview rig follows in rank order
    # and the eye-level rig last
    sv = [RenderView(name=n, path="x") for n in ("overview_top", "Establishing", "eye_front", "BridgeView", "cam_b")]
    assert [v.name for v in rank_views(sv, scene=True)] == ["Establishing", "BridgeView", "cam_b", "overview_top", "eye_front"]


def test_plan_montages_object_rig(tmp_path):
    rs = RenderSet(views=_views(tmp_path, OBJ))
    ms = plan_montages(rs)
    kinds = [m.kind for m in ms]
    assert kinds == ["shaded", "shaded", "shaded", "shaded", "detail", "detail"]
    assert [v.name for v in ms[0].tiles] == ["front_right_high", "back_left_high", "top", "bottom"]
    assert [v.name for v in ms[1].tiles] == ["front", "right", "back", "left"]
    assert [v.name for v in ms[2].tiles] == ["front_right_low", "back_left_low", "front_left_high", "back_right_high"]
    assert [v.name for v in ms[3].tiles] == ["front_left_low", "back_right_low"]
    assert ms[4].crop == (0.25, 0.25, 0.75, 0.75) and ms[5].tiles[0].name == "bottom"
    assert "- image 2: SHADED views (remaining)" in describe_montages(ms)


def test_plan_montages_full_rig_plus_clay_is_the_measured_cprod_plan(tmp_path):
    """14 shaded + 4 clay -> exactly 5 montages + 2 crops; montage 1 keeps the underside
    even under a montage-cap truncation, and one crop is anchored on ``bottom`` (D47)."""
    rs = RenderSet(views=_views(tmp_path, OBJ))
    clay = RenderSet(views=_views(tmp_path, CLAY, mode="clay", prefix="c"))
    ms = plan_montages(rs, geometry_views=clay)  # defaults: max_montages=5, detail_crops=2
    grids = [m for m in ms if not m.is_detail]
    crops = [m for m in ms if m.is_detail]
    assert len(ms) == 7 and len(grids) == 5 and len(crops) == 2
    assert [m.kind for m in grids] == ["shaded", "geometry", "shaded", "shaded", "shaded"]
    assert {v.name for v in grids[0].tiles} == {"front_right_high", "back_left_high", "top", "bottom"}
    assert {v.name for v in grids[1].tiles} == set(CLAY) and all(v.mode == "clay" for v in grids[1].tiles)
    assert [v.name for v in grids[2].tiles] == ["front", "right", "back", "left"]
    assert crops[0].tiles[0].name == "front_right_high"
    assert crops[1].tiles[0].name == "bottom"


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
    # without a sheet the pose_* views form the pose montage
    rs2 = RenderSet(views=_views(tmp_path, OBJ[:4]) + poses)
    ms2 = plan_montages(rs2, detail_crops=0)
    assert [m.kind for m in ms2] == ["shaded", "poses"] and len(ms2[1].tiles) == 3


def test_shuffle_keeps_sets_and_details_last(tmp_path):
    rs = RenderSet(views=_views(tmp_path, OBJ))
    ms = plan_montages(rs)
    sh = shuffle_montages(ms, 7)
    assert shuffle_montages(ms, 7) == sh and shuffle_montages(ms, None) == ms
    assert [m.kind for m in sh[-2:]] == ["detail", "detail"]
    assert {frozenset(v.name for v in m.tiles) for m in sh[:-2]} == {frozenset(v.name for v in m.tiles) for m in ms[:-2]}
    seeds = {tuple((m.kind, tuple(v.name for v in m.tiles)) for m in shuffle_montages(ms, s)) for s in range(6)}
    assert len(seeds) >= 3


def test_labels_and_render(tmp_path):
    rs = RenderSet(views=_views(tmp_path, ("front_right_high", "back_left_high", "front")))
    m = plan_montages(rs, detail_crops=1)[0]
    lbl = montage_label(m, 1, 1)
    assert lbl.startswith("MONTAGE 1/1 — SHADED views: top-left = front_right_high · az 45° el 30°, top-right = back_left_high")
    assert "bottom-left = front · az 0° el 0°" in lbl
    assert montage_strip(m, 1, 1) == "MONTAGE 1/1 — SHADED views"
    png = render_montage(m, cache_dir=tmp_path / "cache", tile_px=256)
    assert png.is_file() and render_montage(m, cache_dir=tmp_path / "cache", tile_px=256) == png  # cached
    with Image.open(png) as im:
        assert im.size[0] == 2 * 256 + 3 * 6 and im.size[1] > 2 * 256  # 2 cols × 2 rows with labels
    crop = plan_montages(rs, detail_crops=1)[-1]
    cpng = render_montage(crop, cache_dir=tmp_path / "cache", tile_px=400)
    with Image.open(cpng) as im:
        assert max(im.size) >= 400
    assert "look for bevels" in montage_label(crop, 1, 1)


def test_missing_tile_is_judge_image_error(tmp_path):
    m = plan_montages(RenderSet(views=[RenderView(name="front", path="/nonexistent/x.png")]), detail_crops=0)[0]
    with pytest.raises(JudgeImageError):
        render_montage(m, cache_dir=tmp_path / "cache")


def test_sheet_helpers(tmp_path):
    src = draw_chair(tmp_path / "a.png", size=600)
    with pytest.raises(ValueError):
        montage_2x2([("a", src)] * 5, tmp_path / "m.png")
    out = montage_2x2([("one", src)], tmp_path / "m1.png", tile=200)
    with Image.open(out) as im:
        assert im.size[0] == 200 + 2 * 6  # single column
    c = crop_region(src, tmp_path / "c.png", (0.25, 0.25, 0.75, 0.75), min_px=600)
    with Image.open(c) as im:
        assert im.size == (600, 600)
    with pytest.raises(ValueError):
        crop_region(src, tmp_path / "c2.png", (0.5, 0.5, 0.5, 0.9))


def test_a_stored_pre_d47_render_set_still_leads_with_its_hero_views():
    """PR #3 review: OBJECT_RANK forgot the legacy names, so a re-judged stored 8-view
    run put {top, front, right, back} in montage 1 and anchored the centre crop on
    'top' — its four sibling constants all kept the legacy names for stored runs."""
    from codeverse.contracts.artifacts import RenderView
    from codeverse.judges.prompt_builder import rank_views

    legacy = ["front_right_34", "back_left_34", "front", "right", "back", "left", "top", "low_front_left"]
    ranked = [v.name for v in rank_views([RenderView(name=n, path=f"/x/{n}.png") for n in legacy], scene=False)]
    assert ranked[:2] == ["front_right_34", "back_left_34"], ranked
