"""Budget continuity across resume + charge-before-persist ordering.

A resume used to rebuild the guard with only ``spent`` restored: ``billed_usd``,
``calls``, the stage/round buckets and the wall clock all restarted at zero, so
after a resume the money/time already spent no longer constrained new work and a
raised ``--max-usd`` granted a FULL fresh cap.  The guard now snapshots and
restores its whole accumulator state (``BudgetSnapshot``), and paid work is
persisted BEFORE the ceiling is enforced: single-shot responses are written to
disk, fan-out siblings are arbitrated/adopted, and planner attempts are booked
the moment each call is paid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.contracts.common import Budget, Language, Usage
from codeverse.contracts.plan import StaticPlan
from codeverse.contracts.run import RunStatus
from codeverse.events import EventLog
from codeverse.orchestrator import BudgetExceeded, BudgetGuard, BudgetSnapshot, RunState
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
    g1 = BudgetGuard(Budget(max_usd=1.0, max_minutes=10.0))
    g1.charge(Usage(backend="gemini", cost_usd=0.6), stage="baseline", round_index=0, label="baseline")
    g1.add(Usage(backend="gemini", cost_usd=0.05), stage="judge", role="judge", round_index=0)
    g1.start_time -= 120  # two minutes of ACTIVE work in the first session
    snap = g1.snapshot()
    assert snap.version == 1 and snap.active_s == pytest.approx(120, abs=2)

    # the exact round-trip run_state.json takes: model_dump(mode="json") → validate
    revived = BudgetSnapshot.model_validate(json.loads(json.dumps(snap.model_dump(mode="json"))))
    g2 = BudgetGuard(Budget(max_usd=1.0, max_minutes=10.0))
    g2.restore(revived)
    assert g2.billed_usd == pytest.approx(0.65) and g2.calls == 2
    assert g2.by_stage == {"baseline": pytest.approx(0.6), "judge": pytest.approx(0.05)}
    assert g2.round_costs(0) == {"baseline": pytest.approx(0.6), "judge": pytest.approx(0.05)}
    # ACTIVE minutes carry over; the downtime between the sessions cost nothing
    assert g2.elapsed_minutes() == pytest.approx(2.0, abs=0.1)
    g2.start_time -= 60  # one more ACTIVE minute in THIS session accumulates on top
    assert g2.elapsed_minutes() == pytest.approx(3.0, abs=0.1)
    # prior spend still constrains new work: 0.65 + 0.5 > 1.0
    with pytest.raises(BudgetExceeded):
        g2.charge(Usage(backend="gemini", cost_usd=0.5), stage="refine")

    # prior ACTIVE time still constrains the wall clock immediately after a resume
    g3 = BudgetGuard(Budget(max_usd=10.0, max_minutes=1.0))
    g3.restore(revived)
    with pytest.raises(BudgetExceeded):
        g3.check()


def test_a_raised_cap_grants_only_the_difference_and_grace_never_persists():
    g1 = BudgetGuard(Budget(max_usd=1.0, max_minutes=10.0))
    g1.charge(Usage(backend="gemini", cost_usd=0.9), stage="baseline")
    g1.grant_grace(usd=5.0, minutes=30.0)  # per-attempt salvage headroom — must NOT survive
    snap = g1.snapshot()
    assert "grace_usd" not in snap.model_dump() and "soft_fraction" not in snap.model_dump()

    g2 = BudgetGuard(Budget(max_usd=1.5, max_minutes=10.0))  # --max-usd raised 1.0 → 1.5
    g2.restore(snap)
    assert g2.grace_usd == 0.0 and g2.grace_minutes == 0.0
    assert g2.remaining()["usd"] == pytest.approx(0.6)  # the difference, never a fresh $1.50
    g2.charge(Usage(backend="gemini", cost_usd=0.5), stage="refine")  # fits
    with pytest.raises(BudgetExceeded):
        g2.charge(Usage(backend="gemini", cost_usd=0.2), stage="refine")


def test_build_context_restores_the_snapshot_and_falls_back_to_legacy_spent(tmp_path, settings):
    spec = make_spec(max_usd=1.0)
    ws = Workspace(tmp_path / "runs" / "r").create()
    track = StaticObjectTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.THREEJS))

    g = BudgetGuard(Budget(max_usd=1.0, max_minutes=10.0))
    g.charge(Usage(backend="gemini", cost_usd=0.4), stage="baseline", round_index=0)
    state = RunState()
    state.extra["budget_snapshot"] = g.snapshot().model_dump(mode="json")
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), state)
    assert ctx.budget.billed_usd == pytest.approx(0.4) and ctx.budget.calls == 1
    assert ctx.budget.round_costs(0)["baseline"] == pytest.approx(0.4)

    # legacy fallback: an old run dir carries only spent_usage (spent restored, as before)
    state2 = RunState()
    state2.extra["spent_usage"] = Usage(backend="gemini", cost_usd=0.3).model_dump(mode="json")
    ctx2 = track.build_context(spec, ws, EventLog(ws.events_path), state2)
    assert ctx2.budget.spent.cost_usd == pytest.approx(0.3)


# --------------------------------------------------------------- ordering: single-shot
def test_a_paid_single_shot_response_is_persisted_when_the_budget_trips(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    guard = BudgetGuard(Budget(max_usd=0.01, max_minutes=10.0))
    answer = ("=== FILE: src/object.js ===\n"
              "export function build(THREE) { return new THREE.Group(); }\n"
              "=== END FILE ===")
    model = FakeChatModel(lambda req: answer, cost=0.05)  # this ONE call crosses max_usd
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
            raise BudgetExceeded("cost $9.99 exceeds max_usd $5.00", spent_usd=9.99, elapsed_min=1.0)
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
    guard = BudgetGuard(Budget(max_usd=5.0, max_minutes=10.0))
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
    guard = BudgetGuard(Budget(max_usd=5.0, max_minutes=10.0))
    ws = Workspace(tmp_path / "run").create()

    def responder(req):
        return {"object_name": "Chair"}  # never validates

    with pytest.raises(PlanningError) as ei:
        run_planner(make_spec(), "fake:planner", StaticPlan, ws,
                    model=FakeChatModel(responder, cost=0.01), budget=guard)
    n = 1 + MAX_VALIDATION_REASKS  # every paid attempt, each booked once
    assert guard.spent.cost_usd == pytest.approx(0.01 * n)
    assert ei.value.usage.cost_usd == pytest.approx(0.01 * n)  # the error still reports the total
