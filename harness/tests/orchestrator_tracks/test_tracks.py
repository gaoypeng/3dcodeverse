"""End-to-end track runs with fakes (no network, no Blender, no node)."""

from __future__ import annotations

import json

import pytest

from codeverse3d.addons import select
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ArticulatedPlan, ScenePlan
from codeverse3d.contracts.run import RunRecord, RunStatus
from codeverse3d.orchestrator import RoundPolicy, RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks import get_track
from codeverse3d.tracks.articulated_object import ArticulatedObjectTrack
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import fake_clock, make_spec
from tests.orchestrator_tracks.fakes import (
    FAIL_MARK,
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
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
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "chair")
    judge = FakeJudge(scores=(0.55, 0.7, 0.85))
    services = FakeServices(contract_errors=1)
    agent = FakeAgent(_agent_writer)
    track = StaticObjectTrack(services=services, judge=judge, agent=agent, planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    # FIXED rounds: 0.85 would once have stopped the run as a pass; the baseline + 3 refine rounds run
    assert isinstance(rec, RunRecord) and rec.status is RunStatus.MAX_ROUNDS
    assert [r.kind for r in rec.rounds] == ["baseline", "refine", "refine", "refine"]
    assert [r.score for r in rec.rounds] == [0.55, 0.7, 0.85, 0.85]
    assert rec.total_usage.cost_usd > 0 and rec.extra["stop_reason"] == "max_rounds"
    assert ws.record_path.is_file() and ws.plan_path.is_file() and ws.state_path.is_file()
    state = RunState.load(ws)
    assert state.status is RunStatus.MAX_ROUNDS and state.completed_rounds == [0, 1, 2, 3]
    assert services.materialized == ["fake"] and (ws.root / "AGENTS.md").is_file()
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    for k in ("run.start", "stage.done", "plan.done", "round.start", "build.done", "gates.done", "judge.done", "round.done", "stop", "run.done"):
        assert k in kinds, k
    # per-round artifacts
    assert (ws.root / "rounds" / "r00.json").is_file() and ws.judge_path(2).is_file() and (ws.gates_dir(1) / "contract.json").is_file()
    assert (ws.renders_dir(0) / "sheet.png").is_file()
    # refine rounds carried gate + judge instructions; the contract error was the first instruction
    assert rec.rounds[1].instructions and rec.rounds[1].instructions[0].startswith("[gate/gate:contract] Seat")
    # baseline prompt is concrete: parts table + acceptance + contract + skeleton reminder
    p0 = agent.jobs[0].prompt
    assert "| Seat |" in p0 and "Acceptance checklist" in p0 and "Y is UP" in p0 and "overlap" in p0
    # the refine round fanned out per part (≥3 file-disjoint groups, threejs)
    labels = [j.label for j in agent.jobs]
    assert sum(1 for lb in labels if lb.startswith("refine_")) >= 3
    assert all("EDIT ONLY THESE FILES" in j.prompt for j in agent.jobs if j.label.startswith("refine_"))
    # git history: one commit per round, and the workspace ends at the last one
    assert len({r.commit for r in rec.rounds}) == 4 and ws.head() == rec.rounds[3].commit
    # provenance (D37): the judge protocol hash sits next to the generator prompt hashes
    assert rec.prompt_hashes["judge"] == FakeJudge.prompt_hash and "generate" in rec.prompt_hashes


def test_static_track_single_shot_with_repair_and_budget_stop(tmp_path, chair_plan, settings):
    spec = make_spec(generator="single-shot:gemini:fake", max_rounds=4, max_minutes=6.0)
    ws = Workspace(tmp_path / "runs" / "chair2")
    n = {"gen": 0}

    def responder(req):
        n["gen"] += 1
        if req.label.startswith("baseline"):  # first build fails, the repair fixes it
            return f"=== FILE: src/object.js ===\n// {FAIL_MARK}\nexport function build(){{}}\n=== END FILE ==="
        return "=== FILE: src/object.js ===\nexport function build(THREE) { return new THREE.Group(); }\n=== END FILE ==="

    model = FakeChatModel(responder, cost=0.004, minutes=0.5)
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5, 0.55, 0.6, 0.62)), model=model,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.THREEJS))
    with fake_clock():
        rec = track.run(spec, ws)
    assert rec.status is RunStatus.BUDGET and rec.rounds[0].build.ok and "repair attempts: 1/2 (fixed)" in rec.rounds[0].notes
    assert rec.rounds[0].score == pytest.approx(0.5)
    assert any(r.label.startswith("r00_baseline_repair") for r in model.requests) or any("Repair" in r.messages[0].text for r in model.requests)
    assert rec.total_usage.cost_usd > 0.03 and ws.record_path.is_file()


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


def test_static_track_failure_writes_failed_record_and_raises(tmp_path, chair_plan, settings):
    spec = make_spec()
    ws = Workspace(tmp_path / "runs" / "chair4")

    class BoomRuntime(FakeRuntime):
        def skeleton(self, ws, plan):
            raise RuntimeError("skeleton exploded")

    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(), agent=FakeAgent(_agent_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings, runtime=BoomRuntime(Language.THREEJS))
    with pytest.raises(RuntimeError, match="skeleton exploded"):
        track.run(spec, ws)
    rec = RunRecord.model_validate(json.loads(ws.record_path.read_text()))
    assert rec.status is RunStatus.FAILED and "skeleton exploded" in rec.error
    assert RunState.load(ws).status is RunStatus.FAILED


def test_static_track_whole_object_refine(tmp_path, chair_plan, settings):
    """cadquery = whole-object language (its runtime names no per-part file): refine is ONE task;
    the prompt carries measured-vs-plan numbers in Z-up."""
    spec = make_spec(language=Language.CADQUERY, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "chair5")
    agent = FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label}\n"})
    track = StaticObjectTrack(services=FakeServices(contract_errors=2), judge=FakeJudge(scores=(0.5, 0.6)), agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.CADQUERY))
    rec = track.run(spec, ws)
    assert [j.label for j in agent.jobs] == ["baseline", "refine"]
    p = agent.jobs[1].prompt
    assert "Measured overall extents" in p and "EDIT ONLY" not in p and "Files in scope: `src/model.py`" in p
    assert rec.status is RunStatus.MAX_ROUNDS


# ----------------------------------------------------------------------------- articulated
def test_articulated_track_adds_pose_views_and_sweep_gate(tmp_path, settings):
    spec = make_spec(Track.ARTICULATED_OBJECT, Language.URDF_BLENDER, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "drawer")
    plan = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    agent = FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label}\n", "src/robot.urdf": "<robot name='x'/>\n"})
    services = FakeServices(sweep_errors=1)
    track = ArticulatedObjectTrack(services=services, judge=FakeJudge(scores=(0.6, 0.9), targets=("Drawer",)), agent=agent,
                                   planner_model=_planner(plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.URDF_BLENDER))
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.MAX_ROUNDS
    r0 = rec.rounds[0]
    assert any(v.name.startswith("pose_") for v in r0.renders.views)
    sweep = next(g for g in r0.gates if g.gate == "joint_sweep")
    assert not sweep.passed and rec.rounds[1].instructions[0].startswith("[gate/gate:joint_sweep] DrawerSlide")
    assert "Joints (the URDF must realise EXACTLY these" in agent.jobs[0].prompt and "| DrawerSlide |" in agent.jobs[0].prompt


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
        return {f: f"// refined by {label}\n" for f in job.prompt.split("EDIT ONLY THESE FILES")[-1].splitlines() if False} or {"src/scene.js": f"// {label}\n"}
    return {"src/scene.js": "// x\n"}


def test_scene_track_stages_and_rounds(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan.assets = [a for a in plan.assets if a.kind == "threejs"]  # blender assets need a Blender runtime → covered separately
    for z in plan.zones:
        z.contents = [c for c in z.contents if c in {a.name for a in plan.assets}]
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=1, prompt="a small harbour at dusk")
    ws = Workspace(tmp_path / "runs" / "harbour")
    agent = FakeAgent(_scene_writer)
    services = FakeServices()
    track = SceneTrack(services=services, judge=FakeJudge(scores=(0.6, 0.7), targets=("Quay", "Water")), agent=agent,
                       planner_model=_planner(plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    rec = track.run(spec, ws)
    labels = [j.label for j in agent.jobs]
    # Quay and Water are both small zones (≤ 3 placements) → ONE batched session owning both files
    # assets ∥ env: the combined stage's three sessions interleave freely; order resumes at zones
    assert set(labels[:3]) == {"asset_fishing_boat", "asset_bollard", "env"} and "zones_quay_water" in labels
    assert "compose" not in labels and labels.index("env") < labels.index("zones_quay_water")  # scene.js is assembled
    assert (ws.src / "assets" / "fishing_boat.js").is_file() and (ws.src / "zones" / "quay.js").is_file() and (ws.src / "scene.js").is_file()
    zone_prompt = next(j.prompt for j in agent.jobs if j.label == "zones_quay_water")
    assert "buildFishingBoat" in zone_prompt and "8.00×3.50×3.00" in zone_prompt and "Neighbouring zones" in zone_prompt
    assert "2 zone modules in ONE session" in zone_prompt and zone_prompt.count("## This zone") == 2
    assert [r.kind for r in rec.rounds] == ["baseline", "refine"] and rec.rounds[0].renders is not None
    assert rec.rounds[0].renders.views and rec.status is RunStatus.MAX_ROUNDS
    st = RunState.load(ws)
    assert {"plan", "skeleton", "assets", "env", "layouts", "zones", "assemble"} <= set(st.stages)
    # scene refine tasks route by file ownership: zone → src/zones/<zone>.js
    assert any("src/zones/quay.js" in i for i in rec.rounds[1].instructions)


def test_scene_track_deterministic_assembler_and_resume(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan.assets = [a for a in plan.assets if a.kind == "threejs"]
    for z in plan.zones:
        z.contents = [c for c in z.contents if c in {a.name for a in plan.assets}]
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "harbour2")
    agent = FakeAgent(_scene_writer)
    services = FakeServices(assemble=True)
    mk = lambda: SceneTrack(services=services, judge=FakeJudge(scores=(0.6,)), agent=agent, planner_model=_planner(plan.model_dump(mode="json")),  # noqa: E731
                            settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    rec = mk().run(spec, ws)
    assert "compose" not in [j.label for j in agent.jobs] and "assembled by fake" in (ws.src / "scene.js").read_text()
    n_jobs = len(agent.jobs)
    rec2 = mk().run(spec, ws, resume=True)
    assert len(agent.jobs) == n_jobs  # all stages + baseline cached / loaded
    assert len(rec2.rounds) == 1 and rec2.rounds[0].commit == rec.rounds[0].commit


def _small_scene_plan():
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan.assets = [a for a in plan.assets if a.kind == "threejs"]
    for z in plan.zones:
        z.contents = [c for c in z.contents if c in {a.name for a in plan.assets}]
    return plan


def test_scene_children_are_stages_and_a_failed_env_never_repays_assets(tmp_path, settings):
    """Review-3 V3-claim3: assets/env/layouts each cache as their OWN stage, so a
    failing sibling leaves the paid asset results cached and resume re-runs only it."""
    plan = _small_scene_plan()
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


def test_a_mood_only_replan_invalidates_the_env_stage(tmp_path, settings):
    """Review-3 V4a: the stage key is the whole plan, so a re-plan differing only in
    `mood` misses the cache (it used to serve an env generated under the old mood)."""
    plan = _small_scene_plan()
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "harbour4")
    agent = FakeAgent(_scene_writer)
    mk = lambda: SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.6, 0.6)), agent=agent,  # noqa: E731
                            planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                            runtime=FakeRuntime(Language.SCENE_THREEJS))
    mk().run(spec, ws)
    assert "env" in [j.label for j in agent.jobs]
    # simulate a `resume --force` re-plan that changed ONLY the mood
    stage_file = ws.root / "stages" / "plan.json"
    data = json.loads(stage_file.read_text())
    assert data["result"]["mood"] != "desolate, horror"
    data["result"]["mood"] = "desolate, horror"
    stage_file.write_text(json.dumps(data))
    agent.jobs.clear()
    mk().run(spec, ws, resume=True)
    assert "env" in [j.label for j in agent.jobs]  # a stale cached env must not be served


def test_get_track_dispatch():
    assert isinstance(get_track("static_object"), StaticObjectTrack)
    assert isinstance(get_track(Track.SCENE), SceneTrack)


def test_a_render_timeout_degrades_the_round_instead_of_failing_the_run(tmp_path, chair_plan, settings):
    """Measured 2026-08-26 (art_verify camera_tripod): the refine round built in 1.2 s, then
    render_glb.mjs hit its 330 s timeout under load and the run was recorded `failed` with a
    judged round 0 on disk.  The round keeps build/gates and skips the judge, so a pick
    hands over the judged round 0."""
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
