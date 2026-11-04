"""scene_blender track plumbing (offline): the asset stage's ``bpy`` check, the stage transport retry."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.agent import AgentResult
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.proc import EventLog
from codeverse3d.tracks import scene_assets as SA
from codeverse3d.tracks.scene import (
    SceneTrack,
)
from codeverse3d.tracks.scene_assets import (
    asset_api_summary,
)
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
    small_scene,
)
from tests.orchestrator_tracks.test_prompts_assets import _ctx

BPY = Language.SCENE_BLENDER


@pytest.fixture
def settings(tmp_path):
    from codeverse3d.config import Settings

    return Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")


# --------------------------------------------------------------------------- the templates over a bpy plan
def _bpy_ctx(tmp_ws, settings, *, agent=None, agent_id="fake:x", services=None):
    plan_obj = small_scene(BPY)
    spec = make_spec(Track.SCENE, BPY)
    ctx = _ctx(tmp_ws, settings, spec, plan_obj, agent_id=agent_id, services=services, agent=agent)
    ctx.extra["asset_api"] = asset_api_summary(plan_obj, {}, language=BPY)
    return ctx, plan_obj


def test_check_bpy_asset_without_a_blender_runtime_and_its_throw_away_workspace(tmp_ws, settings) -> None:
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings)
    missing = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert missing.ran and missing.fatal and "was not written" in missing.errors[0]
    ctx.runtime.skeleton(tmp_ws, plan_obj)
    unavailable = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")   # FakeRuntime has no run_wrapper
    assert unavailable.ok and not unavailable.ran and "checker unavailable" in unavailable.warnings[0]
    scratch = SA._bpy_check_workspace(tmp_ws, "src/assets/bollard.py", "bollard")
    assert scratch.root == (tmp_ws.root / "_assets" / "bollard_check").resolve()
    assert 'ASSETS = {\n    "bollard": "Bollard",\n}' in (scratch.root / "src" / "scene.py").read_text()
    # the wrapper's isolated-stage report maps to the check: a raise is fatal with its line, a clean build is measured

    class _Run:
        def __init__(self, report, ok=True, *, error_type="Timeout", harness_failure=False):
            self.report, self.facts = report, {"unique_tris": 12}
            self.result = SimpleNamespace(ok=ok, error_type=error_type if not ok else "", error_message="killed" if not ok else "",
                                          harness_failure=harness_failure)

    class _Runtime(FakeRuntime):
        def __init__(self, report, ok=True, **result):
            super().__init__(BPY)
            self._report, self._ok, self._result = report, ok, result

        def run_wrapper(self, ws, out_dir, *, zones=None, blend=True, timeout_s=None):
            assert zones == {SA.BPY_CHECK_ZONE: "Check"} and blend is False and ws.root.name == "bollard_check"
            return _Run(self._report, self._ok, **self._result)

    ctx.runtime = _Runtime({"errors": [{"stage": "asset", "name": "bollard", "error_type": "KeyError", "error_message": "'x'",
                                        "error_file": "src/assets/bollard.py", "error_line": 7}]})
    raised = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert raised.fatal and raised.errors == ["src/assets/bollard.py:7: KeyError: 'x'"]
    ctx.runtime = _Runtime({"errors": []}, ok=False)
    killed = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert killed.fatal and "did not finish" in killed.errors[0] and "Timeout" in killed.errors[0]
    ctx.runtime = _Runtime({"errors": [{"stage": "zone", "name": SA.BPY_CHECK_ZONE, "error_type": "KeyError", "error_message": "'bollard'"}]})
    nothing = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert nothing.fatal and "returned no Collection" in nothing.errors[0]


class _TransientOnceAgent(FakeAgent):
    """The first session with ``label`` dies of a provider failure (typed transient); the rest work."""

    def __init__(self, writer, label: str, *, transient: bool = True):
        super().__init__(writer)
        self.label, self.transient, self.died = label, transient, False

    def run(self, job):
        if job.label == self.label and not self.died:
            self.died = True
            self.jobs.append(job)
            return AgentResult(ok=False, exit_reason="error", errors=["rc=247; 503 UNAVAILABLE"], usage=Usage(cost_usd=0.01),
                               transient=self.transient)
        return super().run(job)


def _zone_writer(job, ws):
    return {rel: f"import bpy  # {job.label}\n\n\ndef build(ctx):\n    return ctx.collection\n" for rel in job.files_hint} or {
        "src/env.py": f"import bpy  # {job.label}\n\n\ndef height_at(x, y):\n    return 0.0\n\n\ndef build_env(ctx):\n    return {{}}\n"}


def test_an_env_session_killed_by_the_provider_is_retried_once(tmp_path, settings) -> None:
    """The round loop's transport-retry rule, applied to a stage session (env / zones): ONE re-run under
    the ``.a2`` label when the session died transient and the budget allows; a non-transient failure
    is the stage's verdict."""
    # no assets: the asset stage runs beside the env stage, a different story from this one
    plan_obj = small_scene(BPY).model_copy(update={"assets": []})
    for zone in plan_obj.zones:
        zone.contents = []
    agent = _TransientOnceAgent(_zone_writer, "env", transient=True)
    ws = Workspace(tmp_path / "runs" / "retry")
    track = SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.6,)), agent=agent,
                       planner_model=_planner(plan_obj.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(BPY))
    track.run(make_spec(Track.SCENE, BPY, max_rounds=0), ws)
    labels = [j.label for j in agent.jobs if j.label.startswith("env")]
    events = [e for e in EventLog(ws.events_path).read() if e["event"] == "stage.transport_retry"]
    stage = json.loads((ws.root / "stages" / "env.json").read_text())["result"]   # the cached stage: what a resume serves
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert labels == ["env", "env.a2"] and [(e["stage"], e["label"]) for e in events] == [("env", "env")]
    assert stage["ok"] is True and "src/env.py" in stage["files"] and "generate.silent_bail" not in kinds

@pytest.mark.blender
def test_check_bpy_asset_measures_a_module_in_blender(tmp_ws, settings) -> None:
    """The real check: the skeleton's one-box stub builds in its throw-away workspace, measures at the plan's
    size (Y-up w × h × d off the census GLB) and fails the placeholder rule; a two-part module passes."""
    from codeverse3d.config import get_settings
    from codeverse3d.languages import get_runtime

    if not get_settings().resolve_blender():
        pytest.skip("no Blender binary configured")
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings)
    ctx.runtime = get_runtime(BPY)
    ctx.runtime.skeleton(tmp_ws, plan_obj)
    bollard = next(a for a in plan_obj.assets if a.name == "Bollard")
    stub = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard", expected_size_m=bollard.approx_size_m)
    assert stub.ran and not stub.fatal and not stub.ok and stub.size_m == pytest.approx(bollard.approx_size_m, abs=0.01)
    assert stub.tris == 12 and stub.meshes == 1 and "single low-poly box" in stub.errors[0]
    assert (tmp_ws.root / "_assets" / "bollard_check" / "artifacts" / "census.glb").is_file()
    (tmp_ws.src / "assets" / "bollard.py").write_text(
        "import bmesh\nimport bpy\n\n\ndef _part(ctx, name, size, z, rgb):\n"
        "    mat = bpy.data.materials.new(name + 'Mat')\n"
        "    mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (*rgb, 1.0)\n"
        "    me = bpy.data.meshes.new(name)\n    bm = bmesh.new()\n    bmesh.ops.create_cube(bm, size=1.0)\n"
        "    bmesh.ops.scale(bm, vec=size, verts=bm.verts)\n    bmesh.ops.translate(bm, vec=(0.0, 0.0, z), verts=bm.verts)\n"
        "    bm.to_mesh(me)\n    bm.free()\n    me.materials.append(mat)\n"
        "    ctx.collection.objects.link(bpy.data.objects.new(name, me))\n\n\n"
        "def build_bollard(ctx, variant=0):\n"
        "    _part(ctx, 'BollardBase', (0.3, 0.3, 0.1), 0.05, (0.2, 0.2, 0.22))\n"
        "    _part(ctx, 'BollardPost', (0.2, 0.2, 0.4), 0.3, (0.1, 0.1, 0.12))\n"
        "    return ctx.collection\n")
    good = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard", expected_size_m=bollard.approx_size_m)
    # (FakeServices.measure counts no materials; the real measure does — the placeholder rule reads meshes + tris here)
    assert good.ok and good.ran and good.meshes == 2 and good.tris == 24 and good.min_y == pytest.approx(0.0, abs=0.01), good
    (tmp_ws.src / "assets" / "bollard.py").write_text("import bpy\n\n\ndef build_bollard(ctx, variant=0):\n    raise KeyError('x')\n")
    raised = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert raised.fatal and raised.errors[0].startswith("src/assets/bollard.py:5: KeyError")

