"""Regression tests for review fix batch 2 (orchestrator / lifecycle findings)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Judgment, Severity
from codeverse.contracts.common import Language, Track, Usage
from codeverse.contracts.plan import BBox, PartPlan, ScenePlan, StaticPlan
from codeverse.contracts.run import RoundRecord, RunStatus
from codeverse.orchestrator import RoundPolicy, RunState
from codeverse.proc import EventLog
from codeverse.prompts import load_text
from codeverse.tracks.planner import plan_example
from codeverse.tracks.scene import SceneTrack
from codeverse.tracks.static_object import ObjectPipeline, StaticObjectTrack
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import fake_clock, make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
)


def _planner(plan_dict):
    return FakeChatModel(lambda req: plan_dict)


def _writer(job, ws):
    return {"src/object.js": f"// {job.label} r{job.round}\nexport function build(THREE) {{ return new THREE.Group(); }}\n"}


def degraded_verdict(cost: float = 0.003) -> Judgment:
    return Judgment(rubric="fake_v1", judge_backend="fake", scores={}, overall=0.0, passed=False,
                    summary="judge_error: 503 UNAVAILABLE", usage=Usage(backend="fake", cost_usd=cost),
                    raw=json.dumps({"status": "degraded"}))


class ScriptedJudge(FakeJudge):
    """``script[i]`` is either a float (good verdict) or "degraded"."""

    def __init__(self, script):
        super().__init__(scores=tuple(s for s in script if not isinstance(s, str)) or (0.5,), targets=("Seat",))
        self.script = list(script)
        self.all_calls = []

    def judge(self, inp):
        i = min(len(self.all_calls), len(self.script) - 1)
        self.all_calls.append(inp)
        if self.script[i] == "degraded":
            return degraded_verdict()
        self.calls = self.all_calls[: i]  # FakeJudge indexes by len(calls)
        out = super().judge(inp)
        out = out.model_copy(update={"overall": float(self.script[i]), "passed": float(self.script[i]) >= 0.8,
                                     "scores": {"geometry": float(self.script[i])}})
        return out


# --------------------------------------------------------------------- finding: judge charge after persist (+ best BEFORE budget check)
def test_judged_round_survives_budget_ceiling_crossed_by_judge(tmp_path, chair_plan, settings):
    """steps.py:148 — the judge's own charge used to raise BudgetExceeded BEFORE the round
    was committed/recorded, throwing away a complete judged round (scene r2 case)."""
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    judge = FakeJudge(scores=(0.55,), cost=0.05, minutes=12.0)  # this single verdict crosses max_usd
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=FakeAgent(_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    with fake_clock():
        rec = track.run(spec, ws)
    assert rec.status is RunStatus.BUDGET and rec.extra["stop_reason"] == "budget"
    assert len(rec.rounds) == 1 and rec.rounds[0].judgment is not None
    assert rec.best_round == 0 and rec.final_score == pytest.approx(0.55)  # promoted BEFORE the budget stop
    assert (ws.root / "rounds" / "r00.json").is_file() and ws.judge_path(0).is_file()
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "best.updated" in kinds and kinds.index("best.updated") < kinds.index("stop")
    # the judge cost is still in the totals
    assert rec.total_usage.cost_usd > 0.05


# --------------------------------------------------------------------- finding: finalise with a dirty tree at the best commit
def test_finalise_restores_best_when_aborted_round_dirtied_src(tmp_path, chair_plan, settings):
    """lifecycle.py:263 — a refine round that wrote files but died on the generation charge
    left HEAD at the best commit with foreign src/: the run then shipped unjudged code."""
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    # planner 0.002 + baseline agent 0.01 + judge 0.003 = 0.015 < 0.02; the refine agent's
    # 0.01 charge crosses the ceiling AFTER its files hit the disk.
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.55, 0.7), targets=("Seat",)), agent=FakeAgent(_writer, minutes=6.0),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    with fake_clock():
        rec = track.run(spec, ws)
    assert rec.status is RunStatus.BUDGET and rec.best_round == 0 and len(rec.rounds) == 1
    text = (ws.src / "object.js").read_text()
    assert "baseline r0" in text and "refine" not in text  # restored, not the aborted round's edits
    assert ws.changed_files() == []  # tree clean at the delivered commit
    # finding lifecycle.py:256 — the aborted round's charges survive into run_state for resume
    state = RunState.load(ws)
    spent = state.extra["spent_usage"]  # legacy mirror, kept one release
    assert spent["cost_usd"] == pytest.approx(0.025, abs=1e-6)
    snap = state.extra["budget_snapshot"]
    assert snap["billed_usd"] == pytest.approx(0.025, abs=1e-6) and snap["calls"] > 0


# --------------------------------------------------------------------- finding: spent usage persisted on crash paths
def test_spent_usage_saved_when_a_round_crashes(tmp_path, chair_plan, settings):
    class BoomServices(FakeServices):
        def __init__(self):
            super().__init__()
            self.n = 0

        def contract(self, measurement, plan, tol_m, language=""):
            self.n += 1
            if self.n >= 2:
                raise RuntimeError("gate exploded")
            return super().contract(measurement, plan, tol_m, language)

    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    track = StaticObjectTrack(services=BoomServices(), judge=FakeJudge(scores=(0.55,), targets=("Seat",)), agent=FakeAgent(_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    with pytest.raises(RuntimeError, match="gate exploded"):
        track.run(spec, ws)
    state = RunState.load(ws)
    spent = state.extra["spent_usage"]  # legacy mirror, kept one release
    # planner 0.002 + r0 agent 0.01 + r0 judge 0.003 + r1 agent 0.01 — the r1 charge must not vanish
    assert spent["cost_usd"] == pytest.approx(0.025, abs=1e-6)
    assert state.extra["budget_snapshot"]["billed_usd"] == pytest.approx(0.025, abs=1e-6)


# --------------------------------------------------------------------- finding: degraded judge verdicts are glitches, not scores
def test_degraded_verdict_never_scores_and_run_stops_as_judge_unavailable(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    judge = ScriptedJudge([0.65, "degraded", "degraded"])  # r0 good; r1 degraded, re-judge degraded too
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=FakeAgent(_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.rounds[1].judgment is None and rec.rounds[1].score is None  # never 0.0
    assert rec.best_round == 0 and rec.final_score == pytest.approx(0.65)
    assert rec.extra["stop_reason"] == "judge_unavailable" and rec.status is RunStatus.PLATEAU
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "judge.degraded" in kinds and "judge.retry" in kinds
    # the degraded summary never reaches a refine prompt
    assert len(judge.all_calls) == 3
    # audit f1/f3: both degraded verdicts were PAID and the glitch note survives the
    # round's notes join — money and note reach the persisted record and cost.round
    r1 = rec.rounds[1]
    assert "judge degraded" in r1.notes
    assert r1.usage.cost_usd == pytest.approx(0.016, abs=1e-6)  # agent 0.01 + 2 x degraded 0.003
    saved = json.loads((ws.root / "rounds" / "r01.json").read_text())
    assert "judge degraded" in saved["notes"]
    assert saved["usage"]["cost_usd"] == pytest.approx(0.016, abs=1e-6)
    r1_cost = next(e for e in EventLog(ws.events_path).read()
                   if e["event"] == "cost.round" and e["round"] == 1)
    assert r1_cost["judge_usd"] == pytest.approx(0.003) and r1_cost["waste_reason"] == "unjudged"


def test_degraded_verdict_recovers_via_rejudge_of_same_commit(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    judge = ScriptedJudge([0.55, "degraded", 0.85])  # re-judge of r1 succeeds and passes
    agent = FakeAgent(_writer)
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.PASSED and rec.best_round == 1
    assert rec.rounds[1].score == pytest.approx(0.85)
    # the recovery re-judged the SAME commit: exactly 2 generation rounds ran
    assert [j.round for j in agent.jobs] == [0, 1]
    saved = RoundRecord.model_validate(json.loads((ws.root / "rounds" / "r01.json").read_text()))
    assert saved.judgment is not None and saved.judgment.overall == pytest.approx(0.85)
    # audit f2: the recovery re-emits a CORRECTED cost.round so the audit stream stops
    # counting r01 as wasted=unjudged; f1: every paid verdict is on the round's usage
    r1_costs = [e for e in EventLog(ws.events_path).read()
                if e["event"] == "cost.round" and e["round"] == 1]
    assert len(r1_costs) == 2
    first, corrected = r1_costs
    assert first["wasted"] is True and first["waste_reason"] == "unjudged" and first["corrected"] is False
    assert corrected["corrected"] is True and corrected["wasted"] is False
    assert corrected["score"] == pytest.approx(0.85) and corrected["judge_usd"] == pytest.approx(0.003)
    assert rec.rounds[1].usage.cost_usd == pytest.approx(0.016, abs=1e-6)  # agent + degraded + rejudge
    assert "judge degraded" in rec.rounds[1].notes and "judge degraded" in saved.notes


# --------------------------------------------------------------------- finding: plan stage hash covers the whole Spec
def test_resume_with_raised_budget_does_not_replan_or_reskeleton(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "r")
    planner = _planner(chair_plan.model_dump(mode="json"))
    services = FakeServices()
    mk = lambda: StaticObjectTrack(services=services, judge=FakeJudge(scores=(0.5, 0.6, 0.9)), agent=FakeAgent(_writer),  # noqa: E731
                                   planner_model=planner, settings=settings, runtime=FakeRuntime(Language.THREEJS),
                                   policy=RoundPolicy(max_rounds=1, target=0.8))
    mk().run(spec, ws)
    n_plan_calls = len(planner.requests)
    src_before = (ws.src / "object.js").read_text()
    # the only way to continue a BUDGET-stopped run: raise the budget in the spec
    spec2 = make_spec(max_rounds=1)
    rec2 = mk().run(spec2, ws, resume=True)
    assert len(planner.requests) == n_plan_calls  # plan stage still cached
    assert (ws.src / "object.js").read_text() == src_before or "r0" in (ws.src / "object.js").read_text()
    assert rec2.rounds[0].commit  # prior rounds still loaded
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert kinds.count("stage.cached") >= 2


def test_skeleton_never_reruns_over_existing_rounds(tmp_path, chair_plan, settings):
    class CountingRuntime(FakeRuntime):
        def __init__(self, language):
            super().__init__(language)
            self.skeletons = 0

        def skeleton(self, ws, plan):
            self.skeletons += 1
            return super().skeleton(ws, plan)

    spec = make_spec(max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "r")
    rt = CountingRuntime(Language.THREEJS)
    planner = _planner(chair_plan.model_dump(mode="json"))
    mk = lambda: StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5, 0.6)), agent=FakeAgent(_writer),  # noqa: E731
                                   planner_model=planner, settings=settings, runtime=rt)
    mk().run(spec, ws)
    assert rt.skeletons == 1
    src_before = (ws.src / "object.js").read_text()
    # simulate a skeleton-stage hash change on resume (the failure mode the guard closes)
    state = json.loads(ws.state_path.read_text())
    state["stages"].pop("skeleton")
    ws.state_path.write_text(json.dumps(state))
    mk().run(spec, ws, resume=True)
    assert rt.skeletons == 1, "skeleton must not overwrite agent-authored src/ once rounds exist"
    assert (ws.src / "object.js").read_text() == src_before
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "skeleton.skipped" in kinds


# --------------------------------------------------------------------- finding: scene track rebuild path
def test_scene_refine_emits_rebuild_task_when_build_failed(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS)
    ws = Workspace(tmp_path / "runs" / "r")
    ws.create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState())
    ctx.plan = plan
    (ws.src / "zones").mkdir(parents=True, exist_ok=True)
    (ws.src / "zones" / "quay.js").write_text("// broken\n")
    last = RoundRecord(index=0, kind="baseline",
                       build=BuildResult(ok=False, language="scene_threejs", error_type="ProbeError",
                                         error_message="Cannot read properties of undefined (reading 'update')",
                                         error_file="src/zones/quay.js"),
                       gates=[GateReport(gate="lint:scene_threejs", passed=True)])
    tasks, instructions = track.refine_tasks(ctx, last, [last])
    assert len(tasks) == 1 and tasks[0].kind == "rebuild"
    assert tasks[0].files_hint == ["src/zones/quay.js"]
    assert "ProbeError" in tasks[0].prompt and "Cannot read properties" in tasks[0].prompt
    assert instructions and "rebuild" in instructions[0]


# --------------------------------------------------------------------- finding: plan digest frame order
def test_plan_digest_is_labelled_w_h_d_in_the_measurement_frame():
    plan = StaticPlan(object_name="Cabinet", summary="A cabinet.",
                      overall_bbox=BBox(center=(0, 0, 0.4), extents=(0.48, 0.52, 0.80)),  # Z-up plan: W×D×H
                      parts=[PartPlan(name="Body", role="body", description="box",
                                      bbox=BBox(center=(0, 0, 0.4), extents=(0.48, 0.52, 0.80)))])
    zup = ObjectPipeline().plan_summary(SimpleNamespace(plan=plan, language=Language.BLENDER))
    assert "Overall 0.48×0.80×0.52 m (W×H×D)" in zup  # H and D swapped into the GLB/measurement order
    yup = ObjectPipeline().plan_summary(SimpleNamespace(plan=plan, language=Language.THREEJS))
    assert "Overall 0.48×0.52×0.80 m (W×H×D)" in yup  # identity for Y-up plans


# --------------------------------------------------------------------- finding: articulated prompt origin rule
def test_articulated_prompt_states_negated_pivot_visual_origin():
    text = load_text("tracks/generate_articulated.j2")
    assert "child mesh offset relative to P" not in text
    assert "NEGATED world pivot" in text and "WORLD coordinates" in text
    assert "visual AND collision" in text


# --------------------------------------------------------------------- integration: scene_frames errors reach refine
def test_scene_frames_gate_errors_flow_into_refine_instructions(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS)
    ws = Workspace(tmp_path / "runs" / "r")
    ws.create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState())
    ctx.plan = plan
    frames = GateReport(gate="scene_frames", passed=False, findings=[
        GateFinding(gate="scene_frames", severity=Severity.ERROR, target=plan.cameras[0].name,
                    message="frame is 78% dark (mean luminance 0.04)",
                    fix_hint="raise ambient/hemisphere light intensity or move the camera out of shadow",
                    data={"kind": "dark_frame"})])
    last = RoundRecord(index=0, kind="baseline", build=BuildResult(ok=True, language="scene_threejs"),
                       gates=[GateReport(gate="lint:scene_threejs", passed=True), frames])
    tasks, instructions = track.refine_tasks(ctx, last, [last])
    assert tasks, "a scene_frames ERROR must produce a refine task"
    joined = "\n".join(instructions)
    assert "dark" in joined and "FIX: raise ambient" in joined
