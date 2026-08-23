"""select_judge_views / metrics_path_for (pure python)."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.conventions import SCENE_VIEWS
from codeverse.spatial.render_scene import JUDGE_MAX_VIEWS, metrics_path_for, select_judge_views

AUTHORED = ["Establishing", "BridgeAndPond", "LanternDetail"]


def _rs(times=(0.0, 1.5), authored=AUTHORED, orbit=True) -> RenderSet:
    names = list(authored) + ([v.name for v in SCENE_VIEWS] if orbit else [])
    views = [RenderView(name=n, path=f"/x/{n}_t{t:g}.png", time_s=t) for t in times for n in names]
    return RenderSet(views=views, contact_sheet="/x/sheet.png")


def test_default_selection_is_ten_in_priority_order():
    rs = _rs()
    assert len(rs.views) == 18
    sel = select_judge_views(rs)
    assert len(sel.views) == JUDGE_MAX_VIEWS == 10
    keys = [(v.name, v.time_s) for v in sel.views]
    # authored t=0, first two overviews t=0 always in; first two authored at t=1.5 in
    for n in AUTHORED:
        assert (n, 0.0) in keys
    assert ("overview_front_right", 0.0) in keys and ("overview_back_left", 0.0) in keys
    assert ("Establishing", 1.5) in keys and ("BridgeAndPond", 1.5) in keys
    assert ("LanternDetail", 1.5) not in keys
    assert not any(t == 1.5 and n.startswith(("overview", "eye")) for n, t in keys)
    # disk order preserved; original untouched
    assert keys == sorted(keys, key=lambda k: (k[1], rs.views.index(next(v for v in rs.views if (v.name, v.time_s) == k))))
    assert len(rs.views) == 18 and sel.contact_sheet == rs.contact_sheet


def test_small_sets_pass_through_and_nocustom_dropped():
    rs = _rs(times=(0.0,), orbit=False)
    rs.views.append(RenderView(name="Establishing_nocustom", path="/x/cf.png", time_s=0.0))
    sel = select_judge_views(rs, max_n=10)
    assert [v.name for v in sel.views] == AUTHORED
    assert select_judge_views(RenderSet(), max_n=10).views == []
    assert select_judge_views(rs, max_n=0).views == []


def test_max_n_and_custom_orbit_names():
    rs = _rs()
    sel = select_judge_views(rs, max_n=4)
    assert [(v.name, v.time_s) for v in sel.views] == [(n, 0.0) for n in AUTHORED] + [("overview_front_right", 0.0)]
    # unknown orbit names → everything counts as authored
    sel2 = select_judge_views(rs, max_n=12, orbit_names=["nothing"])
    assert len(sel2.views) == 12 and sum(1 for v in sel2.views if v.time_s == 0.0) == 9


def test_metrics_path_for_locates_sibling_file(tmp_path: Path):
    out = tmp_path / "r01"
    out.mkdir()
    (out / "a_t0.png").write_bytes(b"")
    rs = RenderSet(views=[RenderView(name="a", path=str(out / "a_t0.png"))])
    assert metrics_path_for(rs) is None
    (out / "metrics.json").write_text("{}")
    assert metrics_path_for(rs) == out / "metrics.json"
    assert metrics_path_for(RenderSet(contact_sheet=str(out / "sheet.png"))) == out / "metrics.json"
    assert metrics_path_for(RenderSet()) is None
