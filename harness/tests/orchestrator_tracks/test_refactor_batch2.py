"""Refine grouping and judge-view flags."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.orchestrator import RefineTask, plan_refine_groups
from codeverse3d.tracks.scene import ScenePipeline
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.fakes import (
    FakeServices,
)


# --------------------------------------------------------------------- plan_refine_groups
def _task(target: str, files: list[str] | None = None, priority: int = 2) -> RefineTask:
    return RefineTask(target=target, kind="geometry", instruction=f"fix {target}", priority=priority, files=files or [])


def test_plan_refine_groups_fans_out_only_when_allowed_and_disjoint():
    tasks = [_task("Seat", ["src/parts/seat.js"]), _task("Leg", ["src/parts/leg.js"])]
    groups, parallel = plan_refine_groups(tasks, allow_fanout=True, parallel_min_tasks=2)
    assert parallel and len(groups) == 2

    groups, parallel = plan_refine_groups(tasks, allow_fanout=False, parallel_min_tasks=2)
    assert not parallel and len(groups) == 1
    assert groups[0].files == ["src/parts/leg.js", "src/parts/seat.js"] and len(groups[0].tasks) == 2

    # unknown file ownership collapses into one group even when fan-out is allowed
    groups, parallel = plan_refine_groups([_task("Seat", ["src/parts/seat.js"]), _task("overall")], allow_fanout=True)
    assert not parallel and len(groups) == 1


# --------------------------------------------------------------------- judge-view flags (F30 consumption)
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
