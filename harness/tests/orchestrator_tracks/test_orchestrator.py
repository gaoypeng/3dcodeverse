"""budget / fanout / state / runner / rounds unit tests."""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    ImprovementItem,
    Judgment,
    Severity,
)
from codeverse.contracts.common import Budget, Usage
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.run import RoundRecord
from codeverse.orchestrator import (
    BestSelector,
    BudgetExceeded,
    BudgetGuard,
    RefineTask,
    RoundPolicy,
    RunState,
    StageRunner,
    StopPolicy,
    best_index,
    build_refine_instructions,
    hash_inputs,
    plan_parallel_groups,
)
from codeverse.proc import EventLog, fan_out


def test_best_index_prefers_score_then_task_count_then_recency():
    assert best_index([(0.5, 0), (0.7, 2), (0.7, 1), (0.6, 0)]) == 2
    assert best_index([(0.7, 1), (0.7, 1)]) == 1
    assert best_index([(0.9, 3), (0.8, 0)]) == 0
    with pytest.raises(ValueError):
        best_index([])


# ----------------------------------------------------------------------------- budget
def test_budget_guard_charges_and_raises():
    g = BudgetGuard(Budget(max_minutes=10.0))
    g.charge(Usage(cost_usd=0.02))
    g.charge(Usage(cost_usd=0.02))
    assert g.ok()
    g._active_s = 11 * 60                                        # noqa: SLF001 — past the ceiling
    with pytest.raises(BudgetExceeded) as ei:
        g.charge(Usage(cost_usd=0.02))
    assert "max_minutes" in ei.value.reason and g.spent.cost_usd == pytest.approx(0.06)


def test_budget_guard_time_ceiling():
    g = BudgetGuard(Budget(max_minutes=0.0001), start_time=time.time() - 10)
    with pytest.raises(BudgetExceeded):
        g.check()
    assert g.remaining()["minutes"] == 0.0


# ----------------------------------------------------------------------------- fanout
def test_fan_out_preserves_order_and_captures_exceptions():
    def fn(x: int) -> int:
        if x == 2:
            raise ValueError("two")
        time.sleep(0.01 * (4 - x))
        return x * 10

    res = fan_out([0, 1, 2, 3], fn, max_workers=4, label="t")
    assert res[0] == 0 and res[1] == 10 and res[3] == 30 and isinstance(res[2], ValueError)
    assert fan_out([], fn, 2) == []


# ----------------------------------------------------------------------------- state + runner
def test_run_state_roundtrip(tmp_ws):
    st = RunState()
    st.mark_round_done(0, "abc")
    st.update_best(0, "abc", 0.5)
    st.save(tmp_ws)
    again = RunState.load(tmp_ws)
    assert again is not None and again.best_round == 0 and again.current_round == 1 and again.round_commits[0] == "abc"
    assert RunState.load_or_new(tmp_ws, resume=False).best_round is None


def test_stage_runner_caches_by_input_hash(tmp_ws):
    events = EventLog(tmp_ws.events_path)
    runner = StageRunner(tmp_ws, events)
    calls = []

    def fn():
        calls.append(1)
        return {"x": 1}

    assert runner.stage("s1", fn, inputs={"a": 1}) == {"x": 1}
    assert runner.stage("s1", fn, inputs={"a": 1}) == {"x": 1}
    assert len(calls) == 1
    assert runner.stage("s1", fn, inputs={"a": 2}) == {"x": 1}
    assert len(calls) == 2
    kinds = [e["event"] for e in events.read()]
    assert kinds.count("stage.cached") == 1 and kinds.count("stage.done") == 2
    # resume from disk with a fresh runner + state
    st = RunState.load(tmp_ws)
    r2 = StageRunner(tmp_ws, events, st)
    assert r2.stage("s1", fn, inputs={"a": 2}) == {"x": 1} and len(calls) == 2
    assert runner.result_path("a:b/c").name == "a_b_c.json"


def test_an_unreadable_cached_stage_is_a_miss_not_a_dead_run(tmp_ws):
    """RS-2: inputs_hash covers the INPUTS, never the result model's schema.  A cached
    result that no longer validates (the contract gained a field) or no longer parses
    (a clobbered file) used to escape as ValidationError / JSONDecodeError, which
    BaseTrack.run turns into a FAILED run — so every later `3dcode resume` died the same
    way.  Both must re-run the stage and overwrite the file."""

    class PlanV1(BaseModel):
        name: str

    class PlanV2(BaseModel):
        name: str
        units: str  # added after the cache was written

    events = EventLog(tmp_ws.events_path)
    runner = StageRunner(tmp_ws, events)
    runner.stage("plan", lambda: PlanV1(name="stool"), inputs={"prompt": "a stool"}, model=PlanV1)

    calls = []

    def v2():
        calls.append(1)
        return PlanV2(name="stool", units="m")

    r2 = StageRunner(tmp_ws, events, RunState.load(tmp_ws))
    assert r2.stage("plan", v2, inputs={"prompt": "a stool"}, model=PlanV2).units == "m"
    assert len(calls) == 1, "a cached result that does not validate must re-run the stage"

    r2.result_path("plan").write_text('{"stage": "plan", "inputs_hash": ')  # clobbered mid-write
    r3 = StageRunner(tmp_ws, events, RunState.load(tmp_ws))
    assert r3.stage("plan", v2, inputs={"prompt": "a stool"}, model=PlanV2).units == "m"
    assert len(calls) == 2
    assert [e["event"] for e in events.read()].count("stage.cache_invalid") == 2


def test_stage_runner_raises_and_records_nothing(tmp_ws):
    runner = StageRunner(tmp_ws, EventLog(tmp_ws.events_path))

    def boom():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        runner.stage("bad", boom, inputs="y")
    assert "bad" not in runner.state.stages
    assert hash_inputs({"a": [1, 2]}) == hash_inputs({"a": (1, 2)})


# ----------------------------------------------------------------------------- stop policy
def _round(i: int, score: float | None, errors: int = 0, build_ok: bool = True) -> RoundRecord:
    j = Judgment(rubric="r", scores={"a": score}, overall=score, passed=score >= 0.8) if score is not None else None
    gates = [GateReport(gate="g", passed=errors == 0, findings=[GateFinding(gate="g", severity=Severity.ERROR, message="e")] * errors)]
    return RoundRecord(index=i, kind="x", judgment=j, gates=gates, build=BuildResult(ok=build_ok, language="l"), commit=f"c{i}")


def test_stop_policy_decisions():
    sp = StopPolicy(RoundPolicy(max_rounds=3, plateau_window=2, min_delta=0.02, target=0.8))
    assert sp.evaluate([]).reason == "continue"
    assert sp.evaluate([_round(0, 0.5)]).reason == "continue"
    assert sp.evaluate([_round(0, 0.5)], budget_ok=False).reason == "budget"
    assert sp.evaluate([_round(0, 0.5), _round(1, 0.85)]).reason == "pass"
    # three flat rounds: from r03 on the marginal-value stop answers first (both stop;
    # "diminishing_returns" is the more precise reason — see test_round_economics.py)
    assert sp.evaluate([_round(0, 0.5), _round(1, 0.51), _round(2, 0.515)]).reason == "diminishing_returns"
    flat = StopPolicy(RoundPolicy(max_rounds=3, plateau_window=2, min_delta=0.02, target=0.8, marginal_from_round=99))
    assert flat.evaluate([_round(0, 0.5), _round(1, 0.51), _round(2, 0.515)]).reason == "plateau"
    assert sp.evaluate([_round(0, 0.5), _round(1, 0.6), _round(2, 0.7)]).reason == "continue"
    assert sp.evaluate([_round(0, 0.5), _round(1, 0.6), _round(2, 0.7), _round(3, 0.75)]).reason == "max_rounds"
    # unscored rounds (build failed) do not count as plateau evidence
    assert sp.evaluate([_round(0, 0.5), _round(1, None, build_ok=False), _round(2, None, build_ok=False)]).reason == "continue"


def test_best_selector_prefers_score_then_fewer_errors():
    rounds = [_round(0, 0.5), _round(1, 0.7, errors=2), _round(2, 0.7, errors=0), _round(3, 0.6)]
    assert BestSelector().pick(rounds) == 2
    assert BestSelector().pick([]) is None
    assert BestSelector().pick([_round(0, None, build_ok=False), _round(1, None, build_ok=True)]) == 1


# ----------------------------------------------------------------------------- refine instructions
def test_build_refine_instructions_merges_and_groups(chair_plan):
    judgment = Judgment(rubric="r", scores={}, overall=0.6, passed=False, improvement_plan=[
        ImprovementItem(target="seat", kind="geometry", instruction="thicker seat", priority=2),
        ImprovementItem(target="Backrest", kind="material", instruction="warmer oak", priority=1),
        ImprovementItem(target="Seat", kind="geometry", instruction="duplicate kind", priority=3),
        ImprovementItem(target="overall", kind="assembly", instruction="tighten joints", priority=4),
    ])
    gates = [GateReport(gate="contract", passed=False, findings=[
        GateFinding(gate="contract", severity=Severity.ERROR, target="FrontLeg", message="FrontLeg 30 mm too short", fix_hint="scale y by 1.07"),
        GateFinding(gate="contract", severity=Severity.WARN, target="Seat", message="ignored warn"),
    ])]
    failed = [AcceptanceItem(id="a2", text="Four legs touch the ground", how="visual", priority="must"),
              AcceptanceItem(id="a3", text="should item", how="visual", priority="should")]
    fft = lambda t: [f"src/parts/{t.lower()}.js"] if t not in ("overall",) else ["src/object.js"]  # noqa: E731
    tasks = build_refine_instructions(judgment, gates, failed, chair_plan, file_for_target=fft, max_tasks=6)
    assert [t.source for t in tasks][:2] == ["gate", "acceptance"]
    assert tasks[0].target == "FrontLeg" and "FIX: scale y" in tasks[0].instruction and tasks[0].files == ["src/parts/frontleg.js"]
    assert tasks[1].target == "overall"  # acceptance item not naming a part
    kinds = [(t.target, t.kind) for t in tasks]
    assert ("Seat", "geometry") in kinds and kinds.count(("Seat", "geometry")) == 1  # dedupe + canonical name
    assert len(tasks) <= 6
    groups = plan_parallel_groups(tasks)
    assert len(groups) >= 3 and all(g.files for g in groups)
    files = [f for g in groups for f in g.files]
    assert len(files) == len(set(files))  # file-disjoint
    # unknown files collapse to one group
    one = plan_parallel_groups(tasks + [RefineTask(target="x", kind="geometry", instruction="i", priority=3)])
    assert len(one) == 1
    assert build_refine_instructions(None, [], [], chair_plan) == []


def test_refine_cap_protects_gate_tasks(chair_plan):
    gates = [GateReport(gate="g", passed=False, findings=[GateFinding(gate="g", severity=Severity.ERROR, target=f"P{i}", message=f"e{i}") for i in range(8)])]
    judgment = Judgment(rubric="r", scores={}, overall=0.5, passed=False, improvement_plan=[
        ImprovementItem(target=f"J{i}", kind="geometry", instruction="x", priority=1) for i in range(5)])
    tasks = build_refine_instructions(judgment, gates, [], chair_plan, max_tasks=6)
    assert len(tasks) == 8 and all(t.source == "gate" for t in tasks)
