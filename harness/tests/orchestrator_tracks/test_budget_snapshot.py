"""Budget continuity across resume and charge-before-persist ordering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.contracts.common import Budget, Language, Usage
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.contracts.run import RunStatus
from codeverse3d.orchestrator import BudgetExceeded, BudgetGuard, BudgetSnapshot, RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.generation import GenerationTask, generate_files
from codeverse3d.tracks.planner import MAX_VALIDATION_REASKS, PlanningError
from codeverse3d.tracks.planner import plan as run_planner
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
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
    assert snap.active_s == pytest.approx(120, abs=2)

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
    assert g2.timeout_s(3600, floor_s=0, soft=False) == pytest.approx(7 * 60, abs=6)  # 15 − 8, never a fresh 15


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


# --------------------------------------------------------------- ordering: single-shot
def test_a_paid_single_shot_response_is_persisted_when_the_budget_trips(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    guard = BudgetGuard(Budget(max_minutes=1.0))
    answer = ("=== FILE: src/object.js ===\n"
              "export function build(THREE) { return new THREE.Group(); }\n"
              "=== END FILE ===")

    def slow_call(req):  # the CALL ITSELF crosses the wall-clock ceiling
        guard._active_s = 120.0                          # noqa: SLF001
        return answer

    model = FakeChatModel(slow_call, cost=0.05)  # paid work, booked before the stop
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/object.js"])
    res = generate_files(ws, model=model, task=task, budget=guard)  # must NOT raise
    assert res.ok and (ws.src / "object.js").is_file()
    assert res.transcript_path and Path(res.transcript_path).is_file()
    assert guard.spent.cost_usd == pytest.approx(0.05)  # booked, dollar for dollar
    assert not guard.ok()  # the phase boundary (steps._run_phase) turns this into the stop


def test_single_shot_obeys_the_run_clock(tmp_path):
    """A run past its HARD wall-clock ceiling must not buy a single-shot call at all
    (agent-path symmetry), and a live run's request carries the REMAINING wall clock
    as max_wait_s — never the models' 1800 s retry default.  The truncation retry is
    a second full-price call: it is preflighted the same way."""
    ws = Workspace(tmp_path / "ws").create()
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/object.js"], max_output_tokens=1000)

    calls: list = []
    answer = ("=== FILE: src/object.js ===\n"
              "export function build(THREE) { return new THREE.Group(); }\n"
              "=== END FILE ===")
    model = FakeChatModel(lambda req: (calls.append(req) or answer), cost=0.01)

    past = BudgetGuard(Budget(max_minutes=1.0))
    past._active_s = 120.0                               # noqa: SLF001 — already past 1 min
    with pytest.raises(BudgetExceeded):
        generate_files(ws, model=model, task=task, budget=past)
    assert calls == [] and past.spent.cost_usd == 0.0    # refused BEFORE any model call

    live = BudgetGuard(Budget(max_minutes=10.0))
    live._active_s = 8 * 60.0                            # noqa: SLF001 — 2 minutes left
    res = generate_files(ws, model=model, task=task, budget=live)
    assert res.ok and len(calls) == 1
    assert calls[0].max_wait_s is not None and calls[0].max_wait_s <= 121  # clipped to remaining clock

    # truncation retry: the first response is truncated, then the clock runs out
    class TruncatingModel:
        def __init__(self):
            self.calls = 0

        def generate(self, req):
            from codeverse3d.contracts.chat import ChatResponse
            self.calls += 1
            live2._active_s = 11 * 60.0                  # noqa: SLF001 — ceiling crossed mid-call
            return ChatResponse(text="=== FILE: src/object.js ===\nx", finish_reason="max_tokens",
                                usage=Usage(backend="fake", cost_usd=0.01), raw={})

    live2 = BudgetGuard(Budget(max_minutes=10.0))
    tm = TruncatingModel()
    with pytest.raises(BudgetExceeded):
        generate_files(ws, model=tm, task=task, budget=live2)
    assert tm.calls == 1                                 # the retry was never bought
    assert live2.spent.cost_usd == pytest.approx(0.01)   # ...but the paid first call is booked


# --------------------------------------------------------------- ordering: fan-out siblings
def test_a_budget_tripped_candidate_still_lets_the_sibling_be_adopted(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "bo2")

    def writer(job, ws_):
        if ws_.root.name == "c0":  # <ws>/_cand/c0 trips the ceiling mid-generation
            raise BudgetExceeded("elapsed 9.99 min exceeds max_minutes 5.00", spent_usd=9.99)
        return {"src/object.js": f"// {job.label} in {ws_.root.name}\n"
                                 "export function build(THREE) { return new THREE.Group(); }\n"}

    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS), n_candidates=2)
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.MAX_ROUNDS
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


# --------------------------------------------------------------- ordering: r00 boundary stop
def test_the_adopted_best_of_n_winner_survives_a_boundary_budget_stop(tmp_path, chair_plan, settings, monkeypatch):
    """The winner is adopted + committed + candidates.json written, then the boundary
    ``budget.check()`` trips BEFORE run_round persists r00.  prepare_salvage must say
    yes (the paid, buildable candidate is sitting in src/) so the salvage round
    delivers ONE scored round instead of a 0-round record that re-pays all N on resume."""
    import codeverse3d.tracks.candidates as cand

    real_adopt = cand.adopt_candidate

    def adopt_then_ceiling(ctx, sub_ws):
        real_adopt(ctx, sub_ws)
        ctx.budget._active_s = ctx.spec.budget.max_minutes * 60 + 60  # noqa: SLF001 — clock ran out during candidates

    monkeypatch.setattr(cand, "adopt_candidate", adopt_then_ceiling)

    def writer(job, ws_):
        return {"src/object.js": f"// {job.label} in {ws_.root.name}\n"
                                 "export function build(THREE) { return new THREE.Group(); }\n"}

    ws = Workspace(tmp_path / "runs" / "bo")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS), n_candidates=2)
    rec = track.run(make_spec(max_rounds=0), ws)
    assert rec.status is RunStatus.BUDGET
    assert len(rec.rounds) == 1 and rec.rounds[0].score == pytest.approx(0.6)
    ev = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "budget.salvage" in ev and "budget.salvage_skipped" not in ev


def test_a_skeleton_only_budget_trip_still_salvages_nothing(tmp_path, chair_plan, settings):
    """No candidate ever finished (the ceiling tripped mid-generation): there is no
    adopted winner, so the salvage hook must keep saying no off-scene."""

    def writer(job, ws_):
        raise BudgetExceeded("elapsed 11.0 min exceeds max_minutes 10.0", spent_usd=1.0)

    ws = Workspace(tmp_path / "runs" / "bare")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS), n_candidates=1)
    rec = track.run(make_spec(max_rounds=0), ws)
    assert rec.status is RunStatus.BUDGET and rec.rounds == []
    ev = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "budget.salvage_skipped" in ev
