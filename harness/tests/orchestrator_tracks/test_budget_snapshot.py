"""Budget continuity across resume and charge-before-persist ordering."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeverse.contracts.common import Budget, Language, Usage
from codeverse.contracts.plan import StaticPlan
from codeverse.contracts.run import RunStatus
from codeverse.contracts.spec import RunOptions
from codeverse.orchestrator import BudgetExceeded, BudgetGuard, BudgetSnapshot, RunState
from codeverse.proc import EventLog
from codeverse.tracks.generation import GenerationTask, generate_files
from codeverse.tracks.planner import MAX_VALIDATION_REASKS, PlanningError
from codeverse.tracks.planner import plan as run_planner
from codeverse.tracks.static_object import StaticObjectTrack
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
)


# --------------------------------------------------------------- snapshot / restore
def test_resume_restores_money_calls_and_active_time_but_not_downtime():
    g1 = BudgetGuard(Budget(max_minutes=10.0))
    g1.charge(Usage(backend="gemini", cost_usd=0.6), stage="baseline")
    g1.add(Usage(backend="gemini", cost_usd=0.05), stage="judge")
    g1.start_time -= 120  # two minutes of ACTIVE work in the first session
    snap = g1.snapshot()
    assert snap.version == 1 and snap.active_s == pytest.approx(120, abs=2)

    # the exact round-trip run_state.json takes: model_dump(mode="json") → validate
    revived = BudgetSnapshot.model_validate(json.loads(json.dumps(snap.model_dump(mode="json"))))
    g2 = BudgetGuard(Budget(max_minutes=10.0))
    g2.restore(revived)
    assert g2.billed_usd == pytest.approx(0.65) and g2.calls == 2
    assert g2.by_stage == {"baseline": pytest.approx(0.6), "judge": pytest.approx(0.05)}
    # ACTIVE minutes carry over; the downtime between the sessions cost nothing
    assert g2.elapsed_minutes() == pytest.approx(2.0, abs=0.1)
    g2.start_time -= 60  # one more ACTIVE minute in THIS session accumulates on top
    assert g2.elapsed_minutes() == pytest.approx(3.0, abs=0.1)
    g2.charge(Usage(backend="gemini", cost_usd=0.5), stage="refine")
    assert g2.billed_usd == pytest.approx(1.15) and g2.calls == 3

    # prior ACTIVE time still constrains the wall clock immediately after a resume
    g3 = BudgetGuard(Budget(max_minutes=1.0))
    g3.restore(revived)
    with pytest.raises(BudgetExceeded):
        g3.check()


def test_a_raised_cap_grants_only_the_difference_and_grace_never_persists():
    import time as _t

    g1 = BudgetGuard(Budget(max_minutes=10.0), start_time=_t.time())
    g1.charge(Usage(backend="gemini", cost_usd=0.9), stage="baseline")
    g1._active_s = 8.0 * 60                                      # noqa: SLF001 — 8 of its 10 min
    g1.grant_grace(minutes=30.0)  # per-attempt salvage headroom — must NOT survive
    snap = g1.snapshot()
    assert "grace_minutes" not in snap.model_dump() and "soft_fraction" not in snap.model_dump()

    g2 = BudgetGuard(Budget(max_minutes=15.0))                   # --max-minutes raised 10 → 15
    g2.restore(snap)
    assert g2.grace_minutes == 0.0
    assert g2.billed_usd == pytest.approx(0.9), "the spend is restored, not reset"
    assert g2.remaining()["minutes"] == pytest.approx(7.0, abs=0.1)  # 15 − 8, never a fresh 15


def test_build_context_restores_the_budget_snapshot(tmp_path, settings):
    spec = make_spec()
    ws = Workspace(tmp_path / "runs" / "r").create()
    track = StaticObjectTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.THREEJS))

    g = BudgetGuard(Budget(max_minutes=10.0))
    g.charge(Usage(backend="gemini", cost_usd=0.4), stage="baseline")
    state = RunState()
    state.extra["budget_snapshot"] = g.snapshot().model_dump(mode="json")
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), state)
    assert ctx.budget.billed_usd == pytest.approx(0.4) and ctx.budget.calls == 1
    assert ctx.budget.by_stage["baseline"] == pytest.approx(0.4)

    # a snapshot written before by_round was retired (2026-08-30) still loads: extra keys are ignored
    legacy = dict(state.extra["budget_snapshot"], by_round={"0": {"baseline": 0.4}})
    state2 = RunState()
    state2.extra["budget_snapshot"] = legacy
    ctx2 = track.build_context(spec, ws, EventLog(ws.events_path), state2)
    assert ctx2.budget.billed_usd == pytest.approx(0.4)


def test_the_texture_pass_spend_reaches_the_snapshot_a_resume_restores(tmp_path, chair_plan, settings, monkeypatch):
    """finalise saved the state BEFORE the texture pass charged, so the snapshot a resume
    restored was the PRE-texture one and a $0.12 pack simply vanished from the run's money
    (reproduced 2026-08-30: total_usage 0.217 with cost_by_stage[texture], 0.097 after)."""
    import codeverse.texturing.run as texrun

    monkeypatch.setattr(texrun, "texture_pass",
                        lambda *a, **kw: SimpleNamespace(usage=Usage(backend="gemini", cost_usd=0.12),
                                                         summary=lambda: {"shipped": True}))
    spec = make_spec(max_rounds=0, options=RunOptions(texture=True))
    ws = Workspace(tmp_path / "runs" / "tex")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)),
                              agent=FakeAgent(lambda job, ws_: {"src/object.js": "export function build(THREE) "
                                                                                 "{ return new THREE.Group(); }\n"}),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.extra["texturing"] == {"shipped": True}, "the pass must have run at all"

    snap = RunState.load(ws).extra["budget_snapshot"]
    assert snap["by_stage"]["texture"] == pytest.approx(0.12)
    assert snap["spent"]["cost_usd"] == pytest.approx(rec.total_usage.cost_usd, abs=1e-6)
    # and the guard a resume rebuilds starts from that number, not from the pre-texture one
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState.load(ws))
    assert ctx.budget.by_stage["texture"] == pytest.approx(0.12)


# --------------------------------------------------------------- ordering: single-shot
def test_a_paid_single_shot_response_is_persisted_when_the_budget_trips(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    guard = BudgetGuard(Budget(max_minutes=1.0))
    guard._active_s = 120.0                              # noqa: SLF001 — already past 1 min
    answer = ("=== FILE: src/object.js ===\n"
              "export function build(THREE) { return new THREE.Group(); }\n"
              "=== END FILE ===")
    model = FakeChatModel(lambda req: answer, cost=0.05)  # paid work, booked before the stop
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/object.js"])
    res = generate_files(ws, model=model, task=task, budget=guard)  # must NOT raise
    assert res.ok and (ws.src / "object.js").is_file()
    assert res.transcript_path and Path(res.transcript_path).is_file()
    assert guard.spent.cost_usd == pytest.approx(0.05)  # booked, dollar for dollar
    assert not guard.ok()  # the phase boundary (steps._run_phase) turns this into the stop


# --------------------------------------------------------------- ordering: fan-out siblings
def test_a_budget_tripped_candidate_still_lets_the_sibling_be_adopted(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "bo2")

    def writer(job, ws_):
        if ws_.root.name == "c0":  # <ws>/_cand/c0 trips the ceiling mid-generation
            raise BudgetExceeded("elapsed 9.99 min exceeds max_minutes 5.00",
                                 spent_usd=9.99, elapsed_min=9.99)
        return {"src/object.js": f"// {job.label} in {ws_.root.name}\n"
                                 "export function build(THREE) { return new THREE.Group(); }\n"}

    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS), n_candidates=2)
    rec = track.run(spec, ws)
    assert rec.status in (RunStatus.PLATEAU, RunStatus.PASSED)
    assert len(rec.rounds) == 1 and "best-of-2: selected c1" in rec.rounds[0].notes
    assert "c1" in (ws.src / "object.js").read_text()  # the sibling's code was adopted
    cands = json.loads((ws.root / "rounds" / "candidates.json").read_text())
    assert cands["selected"] == 1 and "BudgetExceeded" in cands["candidates"][0]["notes"]


# --------------------------------------------------------------- ordering: planner attempts
def test_planner_attempt_zero_usage_reaches_the_guard_when_attempt_one_raises(tmp_path):
    guard = BudgetGuard(Budget(max_minutes=10.0))
    ws = Workspace(tmp_path / "run").create()
    calls = {"n": 0}

    def responder(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"object_name": "Chair"}  # schema-invalid → one re-ask
        raise RuntimeError("model fell over mid-retry")

    with pytest.raises(RuntimeError, match="fell over"):
        run_planner(make_spec(), "fake:planner", StaticPlan, ws,
                    model=FakeChatModel(responder, cost=0.01), budget=guard)
    assert calls["n"] == 2
    assert guard.spent.cost_usd == pytest.approx(0.01)  # attempt 0's dollars are booked
    assert guard.by_stage.get("plan") == pytest.approx(0.01) and guard.calls == 1


def test_planning_error_dollars_are_booked_exactly_once(tmp_path):
    guard = BudgetGuard(Budget(max_minutes=10.0))
    ws = Workspace(tmp_path / "run").create()

    def responder(req):
        return {"object_name": "Chair"}  # never validates

    with pytest.raises(PlanningError) as ei:
        run_planner(make_spec(), "fake:planner", StaticPlan, ws,
                    model=FakeChatModel(responder, cost=0.01), budget=guard)
    n = 1 + MAX_VALIDATION_REASKS  # every paid attempt, each booked once
    assert guard.spent.cost_usd == pytest.approx(0.01 * n)
    assert ei.value.usage.cost_usd == pytest.approx(0.01 * n)  # the error still reports the total
