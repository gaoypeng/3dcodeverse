"""Integration wiring from the other packages into the tracks (fix batch 2):
clay geometry views for the judge, scene frame gate + judge view selection,
connectivity language, blender multi-file targets, texture hook."""

from __future__ import annotations

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.orchestrator import RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks import get_track
from codeverse3d.tracks.graphics import GraphicsTrack
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.prompting import file_for_target_factory
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.tracks.static_object import StaticObjectTrack, expected_files
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)
from tests.orchestrator_tracks.test_tracks import _agent_writer, _scene_writer


def _static_run(tmp_path, chair_plan, settings, **spec_kw):
    spec = make_spec(max_rounds=0, **spec_kw)
    ws = Workspace(tmp_path / "runs" / "obj")
    judge = FakeJudge(scores=(0.6,))
    services = FakeServices()
    track = StaticObjectTrack(services=services, judge=judge, agent=FakeAgent(_agent_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    return rec, ws, judge, services


# --------------------------------------------------------------------- (b) clay geometry views reach the judge
def test_object_judge_receives_clay_geometry_views(tmp_path, chair_plan, settings):
    rec, ws, judge, services = _static_run(tmp_path, chair_plan, settings)
    inp = judge.calls[0]
    assert inp.geometry_views is not None and len(inp.geometry_views.views) == 4
    assert {v.name for v in inp.geometry_views.views} == {"front_right_34", "back_left_34", "top", "low_front_left"}
    assert all(v.mode == "clay" for v in inp.geometry_views.views)
    assert services.geometry_renders  # rendered from the round's GLB
    assert (ws.renders_dir(0) / "clay").is_dir()


def test_scene_judge_gets_no_geometry_views(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan.assets = [a for a in plan.assets if a.kind == "threejs"]
    for z in plan.zones:
        z.contents = [c for c in z.contents if c in {a.name for a in plan.assets}]
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "scene")
    judge = FakeJudge(scores=(0.6,))
    services = FakeServices(assemble=True)
    track = SceneTrack(services=services, judge=judge, agent=FakeAgent(_scene_writer),
                       planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                       runtime=FakeRuntime(Language.SCENE_THREEJS))
    rec = track.run(spec, ws)
    assert judge.calls and judge.calls[0].geometry_views is None
    # (a) the scene_frames gate ran (passing empty report without metrics.json)
    frames = [g for g in rec.rounds[0].gates if g.gate == "scene_frames"]
    assert len(frames) == 1 and frames[0].passed
    assert services.geometry_renders == []


# --------------------------------------------------------------------- (d) connectivity hints in the author's frame
def test_connectivity_gate_receives_spec_language(tmp_path, chair_plan, settings):
    rec, ws, judge, services = _static_run(tmp_path, chair_plan, settings)
    assert services.connectivity_languages == ["threejs"]


# --------------------------------------------------------------------- (e) blender multi-file expected files / targets
class BlenderishRuntime(FakeRuntime):
    def __init__(self):
        super().__init__(Language.BLENDER)

    @staticmethod
    def file_for_part(part_name: str) -> str:
        from codeverse3d.conventions import to_snake

        return f"src/parts/{to_snake(part_name)}.py"

    @staticmethod
    def file_for_target(target: str) -> list[str]:
        from codeverse3d.conventions import to_snake

        if target.strip().lower() in ("", "overall", "assembly", "object", "model"):
            return ["src/model.py"]
        return [f"src/parts/{to_snake(target)}.py"]


def test_blender_expected_files_and_whole_object_targets(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER)
    ws = Workspace(tmp_path / "runs" / "b")
    ws.create()
    track = StaticObjectTrack(services=FakeServices(), settings=settings, runtime=BlenderishRuntime())
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState())
    ctx.plan = chair_plan
    files = expected_files(ctx)
    assert files[0] == "src/model.py" and "src/parts/seat.py" in files and "src/parts/front_leg.py" in files
    fft = file_for_target_factory(ctx)
    assert fft is not None
    assert fft("overall") == ["src/model.py"]  # via runtime.file_for_target, never src/object.js
    assert fft("assembly") == ["src/model.py"] and fft("object") == ["src/model.py"]
    assert fft("Seat") == ["src/parts/seat.py"]


# --------------------------------------------------------------------- (f) no texture pass in the run
def test_the_run_itself_never_textures_even_when_asked(tmp_path, chair_plan, settings, monkeypatch):
    """The texture pass belongs to the hand-over of the PICKED round (addons/select.package,
    `3dcode make` after the run), not to finalise (2026-09-22)."""
    import codeverse3d.texturing.run as trun

    def boom(*a, **kw):
        raise AssertionError("texture_pass must not run inside a run")

    monkeypatch.setattr(trun, "texture_pass", boom)
    for tags in ([], ["texture"]):
        rec, ws, judge, services = _static_run(tmp_path / (tags[0] if tags else "plain"), chair_plan, settings, tags=tags)
        assert "texturing" not in rec.extra


# --------------------------------------------------------------------- (i) graphics track still constructs through get_track
def test_get_track_graphics_accepts_options(settings):
    t = get_track("graphics", settings=settings, n_candidates=2)
    assert isinstance(t, GraphicsTrack) and t._n_candidates == 2
    assert isinstance(get_track(Track.GRAPHICS), GraphicsTrack)
