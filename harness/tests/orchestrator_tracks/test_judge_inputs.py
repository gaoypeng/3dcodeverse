"""The track's judge inputs: selected views, artifact paths and plan coordinates."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.tracks.scene import ScenePipeline
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.fakes import FakeServices


def _rs(flags: list[bool | None]) -> RenderSet:
    views = [RenderView(name=f"v{i}", path=f"/tmp/v{i}.png", judge=f) for i, f in enumerate(flags)]
    return RenderSet(views=views, renderer="fake")


def test_judge_view_flags_and_path_reconstruction(tmp_path):
    from types import SimpleNamespace

    from codeverse3d.judges.base import judged_subset, resolve_paths

    pipe = ScenePipeline()
    ctx = SimpleNamespace(services=FakeServices())
    rs = _rs([True, False, True])
    out = pipe.judge_views(ctx, rs)
    assert [v.name for v in out.views] == ["v0", "v2"]

    rs = _rs([True, False, None])
    sub = judged_subset(rs)
    assert [v.name for v in sub.views] == ["v0"]
    assert judged_subset(_rs([None, None])).views == _rs([None, None]).views
    # resolve_paths absolutises the stored out_dir alongside views/sheet
    ws = Workspace(tmp_path / "ws").create()
    rel = RenderSet(views=[RenderView(name="a", path="artifacts/renders/r00/a.png")],
                    contact_sheet="artifacts/renders/r00/sheet.png", out_dir="artifacts/renders/r00")
    fixed = resolve_paths(ws, rel)
    assert fixed.out_dir == str(ws.root / "artifacts/renders/r00")
    assert fixed.views[0].path == str(ws.root / "artifacts/renders/r00/a.png")


def test_plan_digest_is_labelled_w_h_d_in_the_measurement_frame():
    from codeverse3d.judges.base import plan_summary

    plan = StaticPlan(object_name="Cabinet", summary="A cabinet.",
                      overall_bbox=BBox(center=(0, 0, 0.4), extents=(0.48, 0.52, 0.80)),  # Z-up plan: W×D×H
                      parts=[PartPlan(name="Body", role="body", description="box",
                                      bbox=BBox(center=(0, 0, 0.4), extents=(0.48, 0.52, 0.80)))])
    zup = plan_summary(plan, Language.BLENDER)
    assert "Overall 0.48×0.80×0.52 m (W×H×D)" in zup  # H and D swapped into the GLB/measurement order
    yup = plan_summary(plan, Language.THREEJS)
    assert "Overall 0.48×0.52×0.80 m (W×H×D)" in yup  # identity for Y-up plans
