"""Integration wiring into the tracks: runtime file layout, no texture pass inside a run."""

from __future__ import annotations

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)
from tests.orchestrator_tracks.test_tracks import _agent_writer


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


# --------------------------------------------------------------------- (e) every runtime states its file layout
def test_each_runtime_states_which_file_owns_what(chair_plan):
    from codeverse3d.languages import get_runtime

    per_part = {Language.BLENDER: ("src/model.py", ".py"), Language.THREEJS: ("src/object.js", ".js")}
    for lang, (entry, ext) in per_part.items():
        rt = get_runtime(lang)
        assert rt.expected_files(chair_plan) == [entry, *(f"src/parts/{p}{ext}" for p in ("seat", "front_leg", "back_leg",
                                                                                            "backrest", "armrest"))]
        assert rt.files_for(chair_plan, "Seat") == [f"src/parts/seat{ext}"] and rt.files_for(chair_plan, "overall") == [entry]
        assert rt.files_for(chair_plan, "assembly") == rt.files_for(chair_plan, "object") == [entry]
        assert rt.files_for(chair_plan, "BackLeg_1") == []  # an instance name is not a planned part
    whole = {Language.CADQUERY: ["src/model.py"], Language.URDF_BLENDER: ["src/model.py", "src/robot.urdf"],
             Language.GLSL_SHADER: ["src/shader.frag", "src/common.glsl"], Language.OPENGL_PYTHON: ["src/program.py"]}
    for lang, files in whole.items():
        rt = get_runtime(lang)
        assert rt.expected_files(chair_plan) == files and rt.files_for(chair_plan, "Seat") == [] == rt.files_for(chair_plan, "overall")
    scene = get_runtime(Language.SCENE_THREEJS)
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))  # Quay places Bollard (three.js) and Crate (the hero)
    assert scene.expected_files(plan) == ["src/scene.js", "src/env.js"]
    assert scene.files_for(plan, "Quay") == ["src/zones/quay.js"] and scene.files_for(plan, "Bollard") == ["src/assets/bollard.js"]
    assert scene.files_for(plan, "Crate") == ["src/zones/quay.js"]  # a hero is fixed where it is placed
    assert scene.files_for(plan, "Overview") == ["src/scene.js"] and scene.files_for(plan, "fog") == ["src/env.js"]
    assert scene.files_for(plan, "Quay/Bollard") == ["src/zones/quay.js"] and scene.files_for(plan, "overall") == []


# --------------------------------------------------------------------- (f) no texture pass in the run
def test_the_run_itself_never_textures_even_when_asked(tmp_path, chair_plan, settings, monkeypatch):
    """The texture pass belongs to the hand-over of the PICKED round, not to finalise."""
    import codeverse3d.texturing.run as trun

    def boom(*a, **kw):
        raise AssertionError("texture_pass must not run inside a run")

    monkeypatch.setattr(trun, "texture_pass", boom)
    for tags in ([], ["texture"]):
        rec, ws, judge, services = _static_run(tmp_path / (tags[0] if tags else "plain"), chair_plan, settings, tags=tags)
        assert "texturing" not in rec.extra
