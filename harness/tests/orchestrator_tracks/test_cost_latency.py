"""Scene cost/latency shaping: batching, cheap assets, and budget salvage."""

from __future__ import annotations

import json
import shutil

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentResult
from codeverse3d.contracts.common import Budget, Language, Track
from codeverse3d.contracts.plan import AssetPlan, ScenePlan, ZonePlan
from codeverse3d.contracts.run import RunStatus
from codeverse3d.orchestrator import BudgetExceeded, BudgetGuard, RoundPolicy, RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.common import (
    RunContext,
    generate_for,
)
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.tracks.scene_assets import (
    asset_api_summary,
    check_threejs_asset,
    dedupe_assets,
    run_asset_stage,
    select_assets,
    variant_index,
)
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import fake_clock, make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
    small_scene,
)

RUNTIME_JS = get_settings().runtime_js_dir()
needs_node = pytest.mark.skipif(
    not (shutil.which(get_settings().binaries.node or "node") and (RUNTIME_JS / "node_modules" / "three").is_dir()),
    reason="node + runtime_js/node_modules required")


# ----------------------------------------------------------------------------- soft budget
def test_soft_budget_degrades_before_the_hard_cap_and_grace_reopens_it():
    clock = {"t": 0.0}
    g = BudgetGuard(Budget(max_minutes=10.0), soft_fraction=0.55)
    g.start_time = 0.0
    g.elapsed_minutes = lambda: clock["t"]                       # type: ignore[method-assign]
    assert g.soft_ok() and g.timeout_s(3600, floor_s=0) == 330  # 5.5 soft minutes left
    clock["t"] = 6.0
    assert not g.soft_ok() and "soft cap" in g.soft_exceeded()
    assert g.ok()  # the HARD ceiling is untouched: degrade, do not die
    clock["t"] = 11.0
    assert not g.ok()
    with pytest.raises(BudgetExceeded) as ei:
        g.check()
    assert "max_minutes" in ei.value.reason
    assert set(g.summary()) == {"elapsed_min", "max_minutes"}, "the guard keeps no money"
    g.grant_grace(minutes=5.0)
    assert g.ok() and g.hard_minutes == pytest.approx(15.0)
    g.grant_grace(minutes=1.0)  # never shrinks
    assert g.hard_minutes == pytest.approx(15.0)


def test_timeout_is_clipped_to_the_wall_clock_left():
    g = BudgetGuard(Budget(max_minutes=10.0), soft_fraction=0.5)
    assert g.timeout_s(1800, floor_s=60) == pytest.approx(300, abs=2)  # 50 % of 10 min
    assert g.timeout_s(120, floor_s=60) == pytest.approx(120, abs=2)   # never inflates
    g.start_time -= 600  # the run is already over its wall clock
    assert g.timeout_s(1800, floor_s=90) == 90                          # floor, never 0
    assert g.timeout_s(600, floor_s=0, soft=False) == 0                 # ...unless the floor is 0


# ----------------------------------------------------------------------------- dedupe
def _asset(name: str, size: tuple[float, float, float], kind: str = "threejs") -> AssetPlan:
    return AssetPlan(name=name, kind=kind, description=f"a {name}", approx_size_m=size)


def test_dedupe_folds_near_identical_props_into_one_variant_factory():
    assets = [_asset("SteppingStone", (0.4, 0.1, 0.4)), _asset("PondRock", (0.8, 0.5, 0.7)),
              _asset("HeroBoulder", (2.5, 2.0, 2.4)), _asset("StoneLantern", (0.6, 1.4, 0.6))]
    kept, alias = dedupe_assets(assets)
    assert [k.name for k in kept] == ["SteppingStone", "HeroBoulder", "StoneLantern"]
    assert alias == {"PondRock": "SteppingStone"}          # same family, sizes within 3×
    assert variant_index(alias, "PondRock") == 1 and variant_index(alias, "SteppingStone") == 0
    assert "opts.variant" in kept[0].description and "variant 1 = PondRock" in kept[0].description
    # HeroBoulder is 6× the stepping stone → a different prop, not a variant
    assert "HeroBoulder" not in alias


def test_dedupe_is_a_no_op_for_unrelated_assets_and_reaches_the_zone_prompt():
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    kept, alias = dedupe_assets(list(plan.assets))
    assert not alias and len(kept) == len(plan.assets)
    merged = {"PondRock": "SteppingStone"}
    api = asset_api_summary(plan.model_copy(update={"assets": [_asset("SteppingStone", (0.4, 0.1, 0.4)),
                                                              _asset("PondRock", (0.8, 0.5, 0.7))]}), {}, merged)
    assert "buildSteppingStone(THREE, { variant: 1 })" in api and "merged variant" in api


def _zone(name: str, n: int) -> ZonePlan:
    from codeverse3d.contracts.plan import BBox

    return ZonePlan(name=name, description=name, bbox=BBox(center=(0, 0, 0), extents=(10, 5, 10)),
                    contents=[f"A{i}" for i in range(n)])


class _ChatServices(FakeServices):
    """FakeServices that CAN hand out a chat model (so single-shot is reachable)."""

    def __init__(self, model, **kw):
        super().__init__(**kw)
        self._chat = model
        self.chat_ids: list[str] = []

    def chat_model(self, model_id: str):
        self.chat_ids.append(model_id)
        return self._chat


def _module(pascal: str, w: float = 1.0, h: float = 1.0, d: float = 1.0) -> str:
    return (f"import * as THREE from 'three';\nexport function build{pascal}(T = THREE, opts = {{}}) {{\n"
            f"  const g = new T.Group();\n"
            f"  const a = new T.Mesh(new T.BoxGeometry({w}, {h * 0.6}, {d}), new T.MeshStandardMaterial({{ color: 0x886644 }}));\n"
            f"  a.position.y = {h * 0.3}; g.add(a);\n"
            f"  const b = new T.Mesh(new T.ConeGeometry({w * 0.4}, {h * 0.4}, 10), new T.MeshStandardMaterial({{ color: 0x224466 }}));\n"
            f"  b.position.y = {h * 0.8}; g.add(b);\n  return g;\n}}\n")


def _envelope(rel: str, body: str) -> str:
    return f"=== FILE: {rel} ===\n{body}\n=== END FILE ===\n"


def _scene_ctx(tmp_path, settings, *, services, agent=None, plan=None, agent_id="fake-agent:gemini:x",
               max_minutes: float = 10.0) -> RunContext:
    plan = plan or ScenePlan.model_validate(plan_example(Track.SCENE))
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, generator=agent_id, max_minutes=max_minutes)
    ws = Workspace(tmp_path / "ws").create()
    ws.write_json(ws.spec_path, spec)
    rt = FakeRuntime(Language.SCENE_THREEJS)
    ctx = RunContext(spec=spec, ws=ws, events=EventLog(ws.events_path), settings=settings,
                     budget=BudgetGuard(spec.budget, soft_fraction=0.55), runtime=rt, services=services,
                     state=RunState(), policy=RoundPolicy(), track=Track.SCENE, rubric="scene_v1",
                     agent_id=agent_id, plan=plan, agent=agent, contract_text="CONTRACT")
    rt.skeleton(ws, plan)
    ws.commit("skeleton")
    return ctx


def _events(ctx) -> list[dict]:
    return [json.loads(line) for line in ctx.ws.events_path.read_text().splitlines() if line.strip()]


def test_asset_check_names_the_file_that_was_never_written(tmp_path, settings):
    services = FakeServices()
    ctx = _scene_ctx(tmp_path, settings, services=services)
    chk = check_threejs_asset(ctx, "src/assets/missing.js", "Missing")
    assert not chk.ok and chk.ran and chk.fatal and "was not written" in chk.errors[0]


# a CLI session that died at the wall after a 503 streak: nothing written, transient
STORM_DEATH = AgentResult(ok=False, exit_reason="timeout", transient=True,
                          errors=["killed by watchdog (hard_timeout) after 720s", "11 x 503 inside the CLI's own retry loop before the wall; nothing produced"])


def test_a_storm_dead_session_falls_back_to_single_shot(tmp_path, settings):
    """A session that died in a 503 storm goes once more through the single-shot path."""
    def respond(req):
        return _envelope("src/env.js", "export function buildEnv() { return {}; }\n")

    services = _ChatServices(FakeChatModel(respond))
    agent = FakeAgent(lambda j, w: STORM_DEATH)
    ctx = _scene_ctx(tmp_path, settings, services=services, agent=agent, agent_id="fake-agent:gemini:x")
    task = GenerationTask(label="env", prompt="write `src/env.js`", files_hint=["src/env.js"], round=0, kind="env")
    res = generate_for(ctx, task)
    assert res.ok and [c.path for c in res.files_changed] == ["src/env.js"]
    assert len(agent.jobs) == 1 and services.chat_ids == [ctx.spec.backends.planner]   # the planner backend, always an API model
    assert "503 storm" in res.notes and "single-shot" in res.notes
    kinds = [e["event"] for e in _events(ctx)]
    assert "generate.storm_fallback" in kinds


def test_a_zones_session_the_clock_stopped_is_kept_for_the_resume(tmp_path, settings):
    """Q1: the finished (paid) zones session is cached and the resume under the salvaged r00 serves it."""
    plan = small_scene()
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "quay")
    agent = FakeAgent(lambda job, ws_: {rel: "export function build(){}\n" for rel in (job.files_hint or ["src/scene.js"])},
                      cost=1.3, minutes=3.0)
    mk = lambda: SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.58, 0.6)), agent=agent,  # noqa: E731
                            planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                            runtime=FakeRuntime(Language.SCENE_THREEJS))
    with fake_clock():
        rec = mk().run(spec, ws)
        assert rec.status is RunStatus.BUDGET and len(rec.rounds) == 1
        assert "zones" in RunState.load(ws).stages
        agent.jobs.clear()
        mk().run(spec.model_copy(update={"budget": spec.budget.model_copy(update={"max_minutes": 100.0})}), ws, resume=True)
    assert agent.jobs and not any(j.label.startswith("zone") for j in agent.jobs)


def _writer(job, ws):
    if job.label.startswith("zones_") or job.label.startswith("zone_"):
        return {rel: "export function build(){}\n" for rel in (job.files_hint or ["src/zones/x.js"])}
    if job.label == "env":
        return {"src/env.js": "export function buildEnv(){}\n"}
    return {f"src/assets/{job.label[6:]}.js": "export function build(){}\n"}


@needs_node
def test_a_merged_asset_leaves_a_working_shim_not_the_placeholder_box(tmp_path, settings, monkeypatch):
    import codeverse3d.tracks.scene_assets as sa

    monkeypatch.setattr(sa, "MAX_ASSETS", 1)  # force the rescue path with a tiny plan
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan = plan.model_copy(update={"assets": [_asset("PondRock", (1.1, 0.75, 0.9)),
                                              _asset("SteppingStone", (0.65, 0.12, 0.55))], "zones": []})

    def respond(req):
        rel = req.messages[0].text.split("write `")[1].split("`")[0]
        return _envelope(rel, _module("PondRock", 1.1, 0.75, 0.9))

    services = _ChatServices(FakeChatModel(respond), judge=FakeJudge(scores=(0.9,)))
    ctx = _scene_ctx(tmp_path, settings, services=services, agent=FakeAgent(lambda j, w: None), plan=plan)
    results = run_asset_stage(ctx)
    assert set(results) == {"PondRock"}, "the twin is a variant of the survivor, not a second build"
    shim = ctx.ws.src / "assets" / "stepping_stone.js"
    assert shim.is_file() and "variant: 1" in shim.read_text() and "from './pond_rock.js'" in shim.read_text()
    chk = check_threejs_asset(ctx, "src/assets/stepping_stone.js", "SteppingStone")
    assert chk.ok and chk.meshes == 2, "the shim resolves to the real factory, not the blockout stub"


def test_asset_selection_folds_only_over_the_cap():
    twins = [_asset("PondRock", (1.1, 0.75, 0.9)), _asset("SteppingStone", (0.65, 0.12, 0.55))]
    kept, alias = select_assets(twins, cap=8)
    assert [k.name for k in kept] == ["PondRock", "SteppingStone"] and not alias
    kept, alias = select_assets(twins, cap=1)
    assert [k.name for k in kept] == ["PondRock"] and alias == {"SteppingStone": "PondRock"}



def test_a_clock_that_trips_after_a_finished_zones_session_records_what_it_wrote(tmp_path, settings):
    """Q1: the cached zones result records the zones the session wrote before its charge crossed the ceiling."""
    from tests.orchestrator_tracks.conftest import FAKE_CLOCK

    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan = plan.model_copy(update={"assets": [_asset("Bollard", (0.3, 0.5, 0.3))],
                                   "zones": [_zone("Quay", 0), _zone("Water", 0)]})
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0, max_minutes=10.0)
    ws = Workspace(tmp_path / "runs" / "latezone")

    def writer(job, ws_):
        if job.label.startswith("zones_"):
            FAKE_CLOCK["minutes"] += 30.0   # the session ends past the ceiling
            return {"src/zones/quay.js": "export function build(){}\n", "src/zones/water.js": "export function build(){}\n"}
        if job.label == "env":
            return {"src/env.js": "export function buildEnv(){}\n"}
        return {"src/x.js": "// x\n"}

    track = SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                       planner_model=FakeChatModel(lambda req: plan.model_dump(mode="json")), settings=settings,
                       runtime=FakeRuntime(Language.SCENE_THREEJS))
    with fake_clock():
        track.run(spec, ws)
    zones = json.loads((ws.root / "stages" / "zones.json").read_text())["result"]
    assert zones["Quay"]["ok"] and zones["Water"]["ok"] and zones["Water"]["files"] == ["src/zones/water.js"]
    assert "BudgetExceeded" in zones["Quay"]["notes"]


def test_stages_that_spend_the_clock_without_raising_still_get_a_salvaged_round(tmp_path, settings):
    """Live 2026-09-23 (jetty_scene, $1.16): a 503 storm stretched the stages past the ceiling without
    any of them raising (the zones degraded to single-shot and failed quietly), so the round loop's own
    clock check stopped the run at `budget` with NO round: the salvage only ran when a stage RAISED
    BudgetExceeded.  Here the last stage (assemble) is the one that ends past the ceiling."""
    from tests.orchestrator_tracks.conftest import FAKE_CLOCK

    class SlowAssemble(FakeServices):
        def assemble_scene(self, ws, plan):
            FAKE_CLOCK["minutes"] += 30.0
            return super().assemble_scene(ws, plan)

    plan = small_scene()
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=1, max_minutes=10.0)
    ws = Workspace(tmp_path / "runs" / "slowstages")
    track = SceneTrack(services=SlowAssemble(assemble=True), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(_writer),
                       planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                       runtime=FakeRuntime(Language.SCENE_THREEJS))
    with fake_clock():
        rec = track.run(spec, ws)
    names = [json.loads(x)["event"] for x in (ws.root / "events.jsonl").read_text().splitlines() if x.strip()]
    assert rec.status is RunStatus.BUDGET and "budget.salvage" in names, names
    assert len(rec.rounds) == 1 and rec.rounds[0].score == pytest.approx(0.6) and "salvaged" in rec.rounds[0].notes


@needs_node
def test_an_imperfect_asset_stays_available_but_a_broken_one_does_not(tmp_path, settings):
    services = FakeServices()
    ctx = _scene_ctx(tmp_path, settings, services=services)
    (ctx.ws.src / "assets").mkdir(parents=True, exist_ok=True)
    (ctx.ws.src / "assets" / "small.js").write_text(_module("Small", 0.5, 0.5, 0.5))
    off = check_threejs_asset(ctx, "src/assets/small.js", "Small", expected_size_m=(4.0, 4.0, 4.0))
    assert not off.ok and not off.fatal and "rescale" in off.errors[0]
    (ctx.ws.src / "assets" / "broken.js").write_text(
        "import * as THREE from 'three';\nexport function nope() { return 1; }\n")
    bad = check_threejs_asset(ctx, "src/assets/broken.js", "Broken")
    assert not bad.ok and bad.fatal and "missing export" in bad.errors[0]


def test_a_scene_round_judged_at_the_ceiling_keeps_its_verdict(tmp_path, settings):
    plan = small_scene()
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=2)
    ws = Workspace(tmp_path / "runs" / "ceiling")
    judge = FakeJudge(scores=(0.61,), cost=0.5, minutes=12.0)
    track = SceneTrack(services=FakeServices(assemble=True), judge=judge, agent=FakeAgent(_writer, cost=0.001),
                       planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                       runtime=FakeRuntime(Language.SCENE_THREEJS))
    with fake_clock():
        rec = track.run(spec, ws)
    assert rec.status is RunStatus.BUDGET and len(rec.rounds) == 1 and rec.rounds[0].score == pytest.approx(0.61)
    names = [json.loads(x)["event"] for x in (ws.root / "events.jsonl").read_text().splitlines() if x.strip()]
    assert "budget.salvage" not in names, "round 0 already exists: nothing to salvage"
    assert len(judge.calls) == 1


@pytest.mark.node
def test_a_model_outage_escalates_the_asset_instead_of_losing_it(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan = plan.model_copy(update={"assets": [_asset("Bollard", (0.3, 0.5, 0.3))], "zones": []})

    def boom(req):
        raise RuntimeError("Gemini API error 503: This model is currently experiencing high demand.")

    agent = FakeAgent(lambda job, ws: {"src/assets/bollard.js": _module("Bollard", 0.3, 0.5, 0.3)})
    services = _ChatServices(FakeChatModel(boom), judge=FakeJudge(scores=(0.9,)))
    ctx = _scene_ctx(tmp_path, settings, services=services, agent=agent, plan=plan)
    results = run_asset_stage(ctx)
    assert results["Bollard"].ok and results["Bollard"].strategy == "escalated"
    assert [j.label for j in agent.jobs] == ["asset_bollard"]
    failed = [e for e in _events(ctx) if e["event"] == "asset.generate_failed"]
    assert failed and "503" in failed[0]["error"]


@pytest.mark.node
def test_the_shot_after_an_outage_is_a_plain_generation_not_a_repair(tmp_path, settings):
    from codeverse3d.models.base import ModelError

    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan = plan.model_copy(update={"assets": [_asset("Bollard", (0.3, 0.5, 0.3))], "zones": []})
    prompts: list[str] = []

    def respond(req):
        prompts.append(req.messages[0].text)
        if len(prompts) == 1:
            raise ModelError("Gemini API error 503: high demand", retryable=True, status=503)
        return _envelope("src/assets/bollard.js", _module("Bollard", 0.3, 0.5, 0.3))

    agent = FakeAgent(lambda job, ws: {"src/nope.js": "// should not run\n"})
    services = _ChatServices(FakeChatModel(respond, cost=0.02), judge=FakeJudge(scores=(0.9,)))
    ctx = _scene_ctx(tmp_path, settings, services=services, agent=agent, plan=plan)
    results = run_asset_stage(ctx)
    assert results["Bollard"].ok and results["Bollard"].strategy == "single-shot" and not agent.jobs
    assert len(prompts) == 2 and prompts[1] == prompts[0], "the model wrote nothing: there is nothing to repair"
    assert "did NOT pass" not in prompts[1] and "rewrite COMPLETELY" not in prompts[1]
