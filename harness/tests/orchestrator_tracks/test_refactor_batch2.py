"""Refine grouping, RunOptions wiring, and judge-view flags."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import BuildResult, RenderSet, RenderView
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.contracts.run import RunStatus
from codeverse3d.contracts.spec import RunOptions
from codeverse3d.orchestrator import RefineTask, RunState, plan_refine_groups
from codeverse3d.proc import EventLog
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import ScenePipeline
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


def _writer(job, ws):
    return {"src/object.js": f"// {job.label} r{job.round}\nexport function build(THREE) {{ return new THREE.Group(); }}\n"}


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


# --------------------------------------------------------------------- RunOptions wiring (F29)
def test_candidate_width_precedence(tmp_path, settings):
    spec = make_spec(options=RunOptions(candidates=2))
    track = StaticObjectTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.THREEJS))
    ws = Workspace(tmp_path / "spec").create()
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState())
    assert ctx.policy.n_candidates == 2

    ws = Workspace(tmp_path / "constructor").create()
    track = StaticObjectTrack(services=FakeServices(), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS), n_candidates=3)
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState())
    assert ctx.policy.n_candidates == 3


def test_options_texture_triggers_texture_pass(tmp_path, chair_plan, settings, monkeypatch):
    calls = {}

    def fake_texture_pass(ws, spec, plan, **kw):
        from types import SimpleNamespace

        from codeverse3d.contracts.common import Usage

        calls["ws"] = str(ws.root)
        return SimpleNamespace(usage=Usage(backend="fake", cost_usd=0.01), summary=lambda: {"shipped": True})

    import codeverse3d.texturing.run as trun

    monkeypatch.setattr(trun, "texture_pass", fake_texture_pass)
    spec = make_spec(max_rounds=0, options=RunOptions(texture=True))
    ws = Workspace(tmp_path / "runs" / "r")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.9,)), agent=FakeAgent(_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.MAX_ROUNDS
    assert calls, "options.texture must run the texture pass without the legacy tag"
    assert rec.extra.get("texturing") == {"shipped": True}


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


# --------------------------------------------------------------------- judge_context is ctx-free (F3)
def test_judge_context_needs_no_run_context(tmp_path):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    ws = Workspace(tmp_path / "ws").create()
    build = BuildResult(ok=True, language="scene_threejs")
    text = ScenePipeline().judge_context(ws, plan, 0, build, [])
    assert "Environment plan:" in text and "Cameras:" in text
    assert ScenePipeline().judge_context(ws, None, 0, build, []) == ""


def test_graphics_planner_hooks_charge_budget_on_planning_error(tmp_ws):
    import pytest

    from codeverse3d.contracts.plan import GraphicsPlan
    from codeverse3d.contracts.spec import Budget
    from codeverse3d.orchestrator import BudgetGuard
    from codeverse3d.tracks.graphics import GraphicsTrack
    from codeverse3d.tracks.planner import PlanningError
    from codeverse3d.tracks.planner import plan as run_planner

    spec = make_spec(Track.GRAPHICS, Language.GLSL_SHADER)
    budget = BudgetGuard(Budget(max_minutes=10))
    always_bad = FakeChatModel(lambda req: {"title": "x"})
    with pytest.raises(PlanningError):
        run_planner(spec, "fake:planner", GraphicsPlan, tmp_ws, model=always_bad, budget=budget,
                    **GraphicsTrack()._plan_kwargs(spec))
    assert budget.spent.cost_usd > 0, "a failed re-ask is still paid for"

