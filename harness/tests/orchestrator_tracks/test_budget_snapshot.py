"""The budget clock across resume, and paid work that survives a clock stop (its money is
on the ledger, the one record of it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.contracts.common import Budget, Language
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.contracts.run import RunStatus
from codeverse3d.cost.instrument import metered_chat_model, run_ledger
from codeverse3d.cost.ledger import load_ledger
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
def test_resume_restores_active_time_but_not_downtime():
    g1 = BudgetGuard(Budget(max_minutes=10.0))
    g1.start_time -= 120  # two minutes of ACTIVE work in the first session
    snap = g1.snapshot()
    assert snap.active_s == pytest.approx(120, abs=2)

    # the exact round-trip run_state.json takes: model_dump(mode="json") → validate
    revived = BudgetSnapshot.model_validate(json.loads(json.dumps(snap.model_dump(mode="json"))))
    g2 = BudgetGuard(Budget(max_minutes=10.0))
    g2.restore(revived)
    # ACTIVE minutes carry over; the downtime between the sessions cost nothing
    assert g2.elapsed_minutes() == pytest.approx(2.0, abs=0.1)
    g2.start_time -= 60  # one more ACTIVE minute in THIS session accumulates on top
    assert g2.elapsed_minutes() == pytest.approx(3.0, abs=0.1)

    # prior ACTIVE time still constrains the wall clock immediately after a resume
    g3 = BudgetGuard(Budget(max_minutes=1.0))
    g3.restore(revived)
    with pytest.raises(BudgetExceeded):
        g3.check()


def test_build_context_restores_the_budget_snapshot(tmp_path, settings):
    spec = make_spec()
    ws = Workspace(tmp_path / "runs" / "r").create()
    track = StaticObjectTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.THREEJS))

    # a snapshot from before the guard lost its money (2026-09-22) still loads: extra keys are ignored
    legacy = {"spent": {"cost_usd": 0.4}, "billed_usd": 0.4, "calls": 1, "by_stage": {"baseline": 0.4},
              "by_round": {"0": {"baseline": 0.4}}, "active_s": 240.0}
    state2 = RunState()
    state2.extra["budget_snapshot"] = legacy
    ctx2 = track.build_context(spec, ws, EventLog(ws.events_path), state2)
    assert ctx2.budget.elapsed_minutes() == pytest.approx(4.0, abs=0.1)


# --------------------------------------------------------------- ordering: single-shot
def test_a_paid_single_shot_response_is_persisted_when_the_budget_trips_and_the_next_is_not_bought(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    guard = BudgetGuard(Budget(max_minutes=1.0))
    answer = ("=== FILE: src/object.js ===\n"
              "export function build(THREE) { return new THREE.Group(); }\n"
              "=== END FILE ===")

    def slow_call(req):  # the CALL ITSELF crosses the wall-clock ceiling
        guard._active_s = 120.0                          # noqa: SLF001
        return answer

    model = metered_chat_model(FakeChatModel(slow_call, cost=0.05))  # paid work, on the ledger before the stop
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/object.js"])
    with run_ledger(ws.root):
        res = generate_files(ws, model=model, task=task, budget=guard)  # must NOT raise
    assert res.ok and (ws.src / "object.js").is_file()
    assert res.transcript_path and Path(res.transcript_path).is_file()
    assert sum(r.cost_usd for r in load_ledger(ws.root)) == pytest.approx(0.05)  # booked, dollar for dollar
    assert not guard.ok()  # the phase boundary (steps._run_phase) turns this into the stop
    # and once past the ceiling a single-shot buys no call at all
    calls: list = []
    model = FakeChatModel(lambda req: calls.append(req) or "", cost=0.01)
    with pytest.raises(BudgetExceeded):
        generate_files(ws, model=model, task=task.model_copy(update={"max_output_tokens": 1000}), budget=guard)
    assert calls == []                                   # refused BEFORE any model call


# --------------------------------------------------------------- ordering: fan-out siblings
def test_a_budget_tripped_candidate_still_lets_the_sibling_be_adopted(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "bo2")

    def writer(job, ws_):
        if ws_.root.name == "c0":  # <ws>/_cand/c0 trips the ceiling mid-generation
            raise BudgetExceeded("elapsed 9.99 min exceeds max_minutes 5.00")
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
def test_every_paid_planner_attempt_is_booked_once_whether_the_next_raises_or_never_validates(tmp_path):
    guard = BudgetGuard(Budget(max_minutes=10.0))
    ws = Workspace(tmp_path / "run").create()
    calls = {"n": 0}

    def responder(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"object_name": "Chair"}  # schema-invalid → one re-ask
        raise RuntimeError("model fell over mid-retry")

    with run_ledger(ws.root), pytest.raises(RuntimeError, match="fell over"):
        run_planner(make_spec(), "fake:planner", StaticPlan, ws,
                    model=metered_chat_model(FakeChatModel(responder, cost=0.01)), budget=guard)
    assert calls["n"] == 2
    paid, failed = load_ledger(ws.root)  # attempt 0's dollars are booked; the crash is a $0 error row
    assert (paid.stage, paid.cost_usd, failed.outcome, failed.cost_usd) == ("plan", pytest.approx(0.01), "error", 0.0)
    # a plan that never validates: every paid attempt is booked exactly once
    ws = Workspace(tmp_path / "run2").create()
    with run_ledger(ws.root), pytest.raises(PlanningError) as ei:
        run_planner(make_spec(), "fake:planner", StaticPlan, ws,
                    model=metered_chat_model(FakeChatModel(lambda req: {"object_name": "Chair"}, cost=0.01)), budget=guard)
    n = 1 + MAX_VALIDATION_REASKS  # every paid attempt, each booked once
    assert sum(r.cost_usd for r in load_ledger(ws.root)) == pytest.approx(0.01 * n)
    assert ei.value.usage.cost_usd == pytest.approx(0.01 * n)  # the error still reports the total


def test_a_baseline_session_the_clock_stopped_after_it_wrote_code_is_salvaged(tmp_path, chair_plan, settings):
    """E6: code the baseline session left behind is built, rendered and judged as one salvaged round."""

    def writer(job, ws_):
        (ws_.src / "object.js").write_text("// paid for\nexport function build(THREE) { return new THREE.Group(); }\n")
        raise BudgetExceeded("elapsed 11.0 min exceeds max_minutes 10.0")

    ws = Workspace(tmp_path / "runs" / "neon")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS), n_candidates=1)
    rec = track.run(make_spec(max_rounds=0), ws)
    assert rec.status is RunStatus.BUDGET
    assert len(rec.rounds) == 1 and rec.rounds[0].score == pytest.approx(0.6) and "salvaged" in rec.rounds[0].notes
