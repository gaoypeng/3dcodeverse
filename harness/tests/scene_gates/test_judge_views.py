"""select_judge_views / metrics_path_for (pure python)."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.conventions import SCENE_VIEWS
from codeverse3d.spatial.render_scene import JUDGE_MAX_VIEWS, metrics_path_for, select_judge_views

AUTHORED = ["Establishing", "BridgeAndPond", "LanternDetail"]


def _rs(times=(0.0, 1.5), authored=AUTHORED, orbit=True) -> RenderSet:
    names = list(authored) + ([v.name for v in SCENE_VIEWS] if orbit else [])
    views = [RenderView(name=n, path=f"/x/{n}_t{t:g}.png", time_s=t) for t in times for n in names]
    return RenderSet(views=views, contact_sheet="/x/sheet.png")


def test_default_selection_is_authored_plus_overviews():
    rs = _rs()
    assert len(rs.views) == 2 * (3 + 7)
    sel = select_judge_views(rs)
    # 3 authored + the 4 overview tiles (scene_v2: the contact camera is the fourth) + 2 at t=1.5
    assert len(sel.views) == 9 <= JUDGE_MAX_VIEWS == 10
    keys = [(v.name, v.time_s) for v in sel.views]
    # authored t=0, the overview rig t=0, first two authored at t=1.5
    for n in AUTHORED:
        assert (n, 0.0) in keys
    for n in ("overview_front_right", "overview_back_left", "overview_top", "overview_low_front"):
        assert (n, 0.0) in keys, n
    assert ("Establishing", 1.5) in keys and ("BridgeAndPond", 1.5) in keys
    assert ("LanternDetail", 1.5) not in keys
    assert not any(t == 1.5 and n.startswith(("overview", "eye")) for n, t in keys)
    # the harness's eye-level rig is a diagnostic, never a judged picture
    assert not any(n.startswith("eye_") for n, _ in keys)
    # disk order preserved; original untouched
    assert keys == sorted(keys, key=lambda k: (k[1], rs.views.index(next(v for v in rs.views if (v.name, v.time_s) == k))))
    assert len(rs.views) == 20 and sel.contact_sheet == rs.contact_sheet


def test_the_rig_has_four_judged_overviews_and_the_contact_camera_is_the_fourth():
    """scene_v2: ``overview_low_front`` (az 20°, el 7°) is in the rig, judged, and fitted as the
    contact camera (orbit.mjs takes elevation < 10° to fitContactCamera); the eye rig stays diagnostic."""
    from codeverse3d.spatial.render_scene import MAX_JUDGE_OVERVIEWS

    overviews = [v for v in SCENE_VIEWS if v.name.startswith("overview")]
    assert [v.name for v in overviews] == ["overview_front_right", "overview_back_left", "overview_top", "overview_low_front"]
    assert MAX_JUDGE_OVERVIEWS == len(overviews) == 4
    low = next(v for v in SCENE_VIEWS if v.name == "overview_low_front")
    assert (low.azimuth_deg, low.elevation_deg) == (20.0, 7.0) and low.elevation_deg < 10
    assert all(10 <= v.elevation_deg <= 20 for v in SCENE_VIEWS if v.name.startswith("eye_"))


def test_small_sets_pass_through():
    rs = _rs(times=(0.0,), orbit=False)
    sel = select_judge_views(rs, max_n=10)
    assert [v.name for v in sel.views] == AUTHORED
    assert select_judge_views(RenderSet(), max_n=10).views == []
    assert select_judge_views(rs, max_n=0).views == []


def test_metrics_path_for_prefers_stamped_out_dir(tmp_path: Path):
    out = tmp_path / "r02"
    out.mkdir()
    (out / "metrics.json").write_text("{}")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rs = RenderSet(out_dir=str(out), views=[RenderView(name="a", path=str(elsewhere / "a.png"))])
    assert metrics_path_for(rs) == out / "metrics.json"
    # stale out_dir (no metrics.json) → sibling-guessing fallback still works
    (elsewhere / "metrics.json").write_text("{}")
    rs2 = RenderSet(out_dir=str(tmp_path / "gone"), views=[RenderView(name="a", path=str(elsewhere / "a.png"))])
    assert metrics_path_for(rs2) == elsewhere / "metrics.json"
