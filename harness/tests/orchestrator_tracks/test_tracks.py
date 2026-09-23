"""End-to-end track runs with fakes (no network, no Blender, no node)."""

from __future__ import annotations

import json

import pytest

from codeverse3d.addons import select
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.run import RunStatus
from codeverse3d.orchestrator import RoundPolicy, RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.tracks.static_object import StaticObjectTrack
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


def _agent_writer(job, ws):
    """Writes every file named in files_hint-ish prompt: we just write object.js + parts from the prompt table."""
    files = {}
    for line in job.prompt.splitlines():
        if "| " in line and line.startswith("| ") and not line.startswith("| part") and not line.startswith("|---"):
            name = line.split("|")[1].strip()
            if name:
                from codeverse3d.conventions import to_snake

                files[f"src/parts/{to_snake(name)}.js"] = f"export function build{name}(THREE) {{ /* {job.label} r{job.round} */ return new THREE.Group(); }}\n"
    # every round edits something (a real refine agent changes code; identical output = plateau)
    files["src/object.js"] = f"// {job.label} r{job.round}\nexport function build(THREE) {{ return new THREE.Group(); }}\n"
    return files


def test_static_track_end_to_end_agent_path(tmp_path, chair_plan, settings):
    ws = Workspace(tmp_path / "runs" / "chair")
    track = StaticObjectTrack(services=FakeServices(contract_errors=1), judge=FakeJudge(scores=(0.55, 0.7, 0.85)),
                              agent=FakeAgent(_agent_writer), planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.MAX_ROUNDS and [r.score for r in rec.rounds] == [0.55, 0.7, 0.85, 0.85]
    assert rec.rounds[1].instructions[0].startswith("[gate/gate:contract] Seat")   # gate findings lead the refine
    # Law 6: one commit per round, and the workspace ends at the last one
    assert len({r.commit for r in rec.rounds}) == 4 and ws.head() == rec.rounds[3].commit
    # provenance (D37): the judge protocol hash sits next to the generator prompt hashes
    assert rec.prompt_hashes["judge"] == FakeJudge.prompt_hash and "generate" in rec.prompt_hashes


def test_static_track_resume_continues_from_saved_rounds(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=2)
    ws = Workspace(tmp_path / "runs" / "chair3")
    judge = FakeJudge(scores=(0.5, 0.6, 0.9))
    services = FakeServices()
    planner = _planner(chair_plan.model_dump(mode="json"))
    rt = FakeRuntime(Language.THREEJS)
    # first run: policy with max_rounds=0 → stops after the baseline (max_rounds)
    t1 = StaticObjectTrack(services=services, judge=judge, agent=FakeAgent(_agent_writer), planner_model=planner, settings=settings,
                           runtime=rt, policy=RoundPolicy(max_rounds=0))
    rec1 = t1.run(spec, ws)
    assert len(rec1.rounds) == 1 and rec1.status is RunStatus.MAX_ROUNDS and rec1.extra["stop_reason"] == "max_rounds"
    # resume with the full policy: planner must NOT be called again, baseline not re-run
    planner.requests.clear()
    t2 = StaticObjectTrack(services=services, judge=judge, agent=FakeAgent(_agent_writer), planner_model=planner, settings=settings,
                           runtime=rt, policy=RoundPolicy(max_rounds=2))
    rec2 = t2.run(spec, ws, resume=True)
    assert planner.requests == [] and [r.kind for r in rec2.rounds] == ["baseline", "refine", "refine"]
    assert rec2.rounds[0].commit == rec1.rounds[0].commit and rec2.status is RunStatus.MAX_ROUNDS
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert kinds.count("stage.cached") >= 2  # plan + skeleton cached on resume


# ----------------------------------------------------------------------------- scene
def _scene_writer(job, ws):
    label = job.label
    if label.startswith("asset_"):
        return {f"src/assets/{label[6:]}.js": f"export function build(){{}} // {label}\n"}
    if label == "env":
        return {"src/env.js": "export function buildEnv(){}\n"}
    if label.startswith("zones_"):  # batched small zones: ONE session owns several files
        return {rel: f"export function build(){{}} // {label}\n" for rel in job.files_hint}
    if label.startswith("zone_"):
        return {f"src/zones/{label[5:]}.js": f"export function build(){{}} // {label}\n"}
    if label.startswith("refine"):
        return {"src/scene.js": f"// {label}\n"}
    return {"src/scene.js": "// x\n"}


def test_scene_children_are_stages_and_a_failed_env_never_repays_assets(tmp_path, settings):
    """assets/env/layouts cache as their own stages: a failing sibling never re-pays the others."""
    plan = small_scene()
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "harbour3")
    boom = {"on": True}

    def _writer(job, ws_):
        if boom["on"] and job.label == "env":
            raise RuntimeError("env agent session died")
        return _scene_writer(job, ws_)

    agent = FakeAgent(_writer)
    mk = lambda: SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.6,)), agent=agent,  # noqa: E731
                            planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                            runtime=FakeRuntime(Language.SCENE_THREEJS))
    with pytest.raises(RuntimeError, match="env agent session died"):
        mk().run(spec, ws)
    st = RunState.load(ws)
    assert "assets" in st.stages and "layouts" in st.stages and "env" not in st.stages
    assert any(j.label.startswith("asset_") for j in agent.jobs)  # the siblings really ran before the failure
    boom["on"] = False
    agent.jobs.clear()
    rec = mk().run(spec, ws, resume=True)
    labels = [j.label for j in agent.jobs]
    assert "env" in labels and not any(label.startswith("asset_") for label in labels)
    assert len(rec.rounds) == 1


def test_a_mood_only_replan_is_served_from_the_stages_until_a_forced_resume(tmp_path, settings):
    """A scene run with r00 whose plan then changes ONLY in `mood` (the spec does not change).
    F5: with r00 on disk, the drifted key serves the recorded stages instead of re-paying them.
    Q2: the stage key is the whole plan; `resume --force` archives the rounds, so it re-runs env."""
    plan = small_scene()
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "harbour4")
    agent = FakeAgent(_scene_writer)
    mk = lambda: SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.6, 0.6)), agent=agent,  # noqa: E731
                            planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                            runtime=FakeRuntime(Language.SCENE_THREEJS))
    mk().run(spec, ws)
    assert "env" in [j.label for j in agent.jobs]
    stage_file = ws.root / "stages" / "plan.json"
    data = json.loads(stage_file.read_text())
    assert data["result"]["mood"] != "desolate, horror"
    data["result"]["mood"] = "desolate, horror"
    stage_file.write_text(json.dumps(data))

    def resume(force: bool) -> tuple[list[str], list[str]]:
        agent.jobs.clear()
        n_events = len(EventLog(ws.events_path).read())
        mk().run(spec, ws, resume=True, force=force)
        return [j.label for j in agent.jobs], [e["event"] for e in EventLog(ws.events_path).read()[n_events:]]

    labels, events = resume(force=False)
    assert labels == []
    assert events.count("stage.frozen") >= 3  # env, zones, assets (at least)
    assert "stage.start" not in events
    labels, events = resume(force=True)
    assert "env" in labels  # a stale cached env must not be served
    assert "resume.archived" in events and "resume.spec_changed" not in events and "stage.frozen" not in events
    assert (ws.root / "rounds" / "pre_force" / "r00.json").is_file()


def test_a_render_timeout_degrades_the_round_instead_of_failing_the_run(tmp_path, chair_plan, settings):
    from codeverse3d.spatial.render import RenderError

    class _Services(FakeServices):
        def render_object(self, glb, out_dir, *, views, width, height):
            if "r00" not in str(out_dir):
                raise RenderError("render_glb failed: node script render_glb.mjs timed out after 330s")
            return super().render_object(glb, out_dir, views=views, width=width, height=height)

    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "chair")
    track = StaticObjectTrack(services=_Services(contract_errors=1), judge=FakeJudge(scores=(0.55, 0.7, 0.85)),
                              agent=FakeAgent(_agent_writer), planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.status is not RunStatus.FAILED and select.pick(ws.root) == 0
    r1 = rec.rounds[1]
    assert r1.build is not None and r1.build.ok and r1.renders is None and r1.judgment is None
    assert "render failed" in r1.notes
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "render.failed" in kinds and "run.done" in kinds and "run.failed" not in kinds
