"""Round stop economics, session limits, judge skipping, and cost attribution."""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Judgment, Severity
from codeverse.contracts.common import Language, Usage
from codeverse.contracts.run import RoundRecord, RunStatus
from codeverse.orchestrator import (
    DEFAULT_JUDGE_SIGMA,
    REWRITE_KIND,
    RoundPolicy,
    StopPolicy,
    best_score,
    judge_sigma,
    last_gain,
)
from codeverse.proc import EventLog
from codeverse.tracks.generation import (
    DEFAULT_AGENT_MAX_TURNS,
    GenerationTask,
    agent_max_turns,
    run_agent_task,
    turn_capped,
)
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.static_object import StaticObjectTrack
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import fake_clock, make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


def _round(i: int, score: float | None, kind: str = "refine") -> RoundRecord:
    j = Judgment(rubric="r", scores={}, overall=score, passed=bool(score and score >= 0.8)) if score is not None else None
    return RoundRecord(index=i, kind="baseline" if i == 0 else kind, judgment=j,
                       build=BuildResult(ok=True, language="l"), commit=f"c{i}")


# ----------------------------------------------------------------------------- judge noise table
def test_judge_sigma_comes_from_the_one_measured_table():
    from codeverse.cost.routing import JUDGE_NOISE

    assert judge_sigma("gemini:gemini-3.1-pro-preview") == pytest.approx(JUDGE_NOISE["gemini-3.1-pro-preview"][0])
    assert judge_sigma("gemini:gemini-3.7-flash") == pytest.approx(0.083)
    assert judge_sigma("gemini:gemini-3.7-flash-002") == pytest.approx(0.083)  # version suffix
    assert judge_sigma("") == DEFAULT_JUDGE_SIGMA and judge_sigma("who:knows") == DEFAULT_JUDGE_SIGMA
    pol = RoundPolicy(judge_model="gemini:gemini-3.7-flash")
    assert pol.sigma == pytest.approx(0.083)
    assert pol.regression_delta == pytest.approx(0.083) and pol.marginal_delta == pytest.approx(0.1245)


def test_best_score_and_last_gain():
    h = [_round(0, 0.5), _round(1, 0.62), _round(2, 0.55)]
    assert best_score(h) == pytest.approx(0.62) and last_gain(h) == pytest.approx(-0.07)
    assert best_score([]) is None and last_gain([_round(0, 0.5)]) is None
    assert last_gain([_round(0, 0.5), _round(1, None)]) is None


# ----------------------------------------------------------------------------- regression
def test_a_regression_buys_a_change_of_shape_then_stops():
    sp = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, judge_model="gemini:gemini-3.1-pro-preview",
                                marginal_from_round=99))
    inside = sp.evaluate([_round(0, 0.60), _round(1, 0.58)])          # −0.02 < σ 0.030 → judge noise
    assert inside.reason == "continue" and inside.strategy == "same"
    first = sp.evaluate([_round(0, 0.60), _round(1, 0.52)])           # −0.08: a real regression
    assert first.reason == "continue" and first.strategy == "switch" and "0.520" in first.detail
    again = sp.evaluate([_round(0, 0.60), _round(1, 0.52), _round(2, 0.50, kind=REWRITE_KIND)])
    assert again.reason == "regression" and "changed strategy" in again.detail
    twice = sp.evaluate([_round(0, 0.60), _round(1, 0.52), _round(2, 0.55)])  # two regressions vs best
    assert twice.reason == "regression"


def test_a_sub_noise_dip_never_burns_the_strategy_switch():
    from codeverse.orchestrator import meaningful_regression

    sp = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, judge_model="gemini:gemini-3.1-pro-preview",
                                marginal_from_round=99))
    d = sp.evaluate([_round(0, 0.60), _round(1, 0.58), _round(2, 0.50)])
    assert (d.reason, d.strategy) == ("continue", "switch"), "the single switch must still be offered"
    pol = RoundPolicy(judge_model="gemini:gemini-3.1-pro-preview")
    assert not meaningful_regression(0.58, 0.60, pol)  # sub-noise dip: not a regression
    assert meaningful_regression(0.50, 0.60, pol)      # a real one
    assert not meaningful_regression(None, 0.60, pol) and not meaningful_regression(0.5, None, pol)


def test_a_noisy_judge_widens_the_regression_band():
    flash = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, marginal_from_round=99,
                                   judge_model="gemini:gemini-3.7-flash"))
    # −0.06 is a regression for pro (σ 0.030) but inside flash's own noise (σ 0.083)
    assert flash.evaluate([_round(0, 0.60), _round(1, 0.54)]).strategy == "same"
    pro = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, marginal_from_round=99,
                                 judge_model="gemini:gemini-3.1-pro-preview"))
    assert pro.evaluate([_round(0, 0.60), _round(1, 0.54)]).strategy == "switch"


# ----------------------------------------------------------------------------- diminishing returns
def test_r03_only_runs_when_the_last_round_paid_for_itself():
    sp = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, judge_model="gemini:gemini-3.1-pro-preview"))
    # r02 gained +0.10 ≫ 1.5σ = 0.045 → r03 is worth starting
    assert sp.evaluate([_round(0, 0.4), _round(1, 0.5), _round(2, 0.6)]).reason == "continue"
    # r02 gained +0.03 < 0.045 → not worth another round
    d = sp.evaluate([_round(0, 0.40), _round(1, 0.55), _round(2, 0.58)])
    assert d.reason == "diminishing_returns" and "1.5 × judge σ" in d.detail
    # ... and the rule does not apply to r01/r02, which the audit shows are the good buys
    assert sp.evaluate([_round(0, 0.40), _round(1, 0.42)]).reason == "continue"
    # scoring at/above target without passing (a failed must-item) is not worth
    # another refine round either — the score is not what is missing
    above = RoundRecord(index=2, kind="refine", build=BuildResult(ok=True, language="l"),
                        judgment=Judgment(rubric="r", scores={}, overall=0.95, passed=False))
    at_target = sp.evaluate([_round(0, 0.4), _round(1, 0.6), above])
    assert at_target.reason == "diminishing_returns" and "target" in at_target.detail


def test_the_marginal_round_is_configurable():
    sp = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, marginal_from_round=2,
                                judge_model="gemini:gemini-3.1-pro-preview"))
    assert sp.evaluate([_round(0, 0.40), _round(1, 0.42)]).reason == "diminishing_returns"
    # the detail states the multiple that was APPLIED, not the default one
    wide = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, marginal_sigma=3.0,
                                  judge_model="gemini:gemini-3.1-pro-preview"))
    d = wide.evaluate([_round(0, 0.40), _round(1, 0.50), _round(2, 0.57)])
    assert d.reason == "diminishing_returns" and "3 × judge σ" in d.detail and "1.5 ×" not in d.detail


def test_the_stop_order_is_exhausted_regression_then_marginal_then_switch_then_plateau():
    pro = dict(max_rounds=4, target=0.9, judge_model="gemini:gemini-3.1-pro-preview")
    sp = StopPolicy(RoundPolicy(**pro))
    # 1. the switch is already spent (a rewrite regressed again) — outranks everything,
    #    even though the marginal test would also stop here (last gain −0.10)
    spent = [_round(0, 0.60), _round(1, 0.52), _round(2, 0.50, kind=REWRITE_KIND)]
    assert sp.evaluate(spent).reason == "regression" and "changed strategy" in sp.evaluate(spent).detail
    # 2. at r03 the marginal test beats a FIRST regression: no shape is worth $0.5 there
    marginal = [_round(0, 0.60), _round(1, 0.65), _round(2, 0.55)]
    assert sp.evaluate(marginal).reason == "diminishing_returns"
    # 3. before r03 the same first regression buys exactly one change of shape
    switch = sp.evaluate([_round(0, 0.60), _round(1, 0.52)])
    assert (switch.reason, switch.strategy, switch.stop) == ("continue", "switch", False)
    # 4. plateau is the fallback: no regression, marginal test not in force, no gain
    flat = StopPolicy(RoundPolicy(max_rounds=4, target=0.9, marginal_from_round=99, plateau_window=2,
                                  min_delta=0.02, judge_model="gemini:gemini-3.1-pro-preview"))
    assert flat.evaluate([_round(0, 0.50), _round(1, 0.505), _round(2, 0.51)]).reason == "plateau"
    # ... and a run that is still climbing is never stopped by any of the four
    assert flat.evaluate([_round(0, 0.50), _round(1, 0.60), _round(2, 0.70)]).reason == "continue"


# ----------------------------------------------------------------------------- turn budget
class _TurnAgent:
    """Burns turns; reports 'max_turns (N) reached' when it runs out."""

    kind = "fake"
    model = "gemini:gemini-3.7-flash"

    def __init__(self, *, writes_on: int = 2, cost: float = 0.2):
        self.jobs: list[AgentJob] = []
        self.writes_on = writes_on
        self.cost = cost

    def run(self, job: AgentJob) -> AgentResult:
        self.jobs.append(job)
        ws = Workspace(job.workspace)
        wrote = []
        if len(self.jobs) >= self.writes_on:
            (ws.src).mkdir(parents=True, exist_ok=True)
            (ws.src / "object.js").write_text(f"// session {len(self.jobs)}\n")
            wrote = [{"path": "src/object.js", "status": "modified"}]
        exhausted = len(self.jobs) == 1
        return AgentResult(ok=bool(wrote), exit_reason="budget" if exhausted else "completed",
                           files_changed=wrote, errors=[f"max_turns ({job.max_turns}) reached"] if exhausted else [],
                           usage=Usage(backend="api-agent", cost_usd=self.cost, input_tokens=1000))


def test_agent_sessions_are_uncapped_by_default(tmp_ws, monkeypatch):
    monkeypatch.delenv("CV3D_AGENT_MAX_TURNS", raising=False)
    assert DEFAULT_AGENT_MAX_TURNS == 0 and RoundPolicy().agent_max_turns == 0
    agent = _TurnAgent(writes_on=1)
    task = GenerationTask(label="baseline", prompt="p", round=0, kind="baseline")
    run_agent_task(tmp_ws, agent=agent, task=task)
    assert agent.jobs[0].max_turns == AgentJob(workspace="w", prompt="p").max_turns  # the backend's own default
    assert agent_max_turns() == 0  # "no cap asked for"
    # ... and every explicit way of asking for one still works
    run_agent_task(tmp_ws, agent=agent, task=task, max_turns=12)
    assert agent.jobs[-1].max_turns == 12
    run_agent_task(tmp_ws, agent=agent, task=task.model_copy(update={"max_turns": 7}), max_turns=12)
    assert agent.jobs[-1].max_turns == 7  # the task wins over the policy
    monkeypatch.setenv("CV3D_AGENT_MAX_TURNS", "9")
    assert agent_max_turns() == 9
    run_agent_task(tmp_ws, agent=agent, task=task)
    assert agent.jobs[-1].max_turns == 9
    monkeypatch.setenv("CV3D_AGENT_MAX_TURNS", "nonsense")
    assert agent_max_turns() == DEFAULT_AGENT_MAX_TURNS == 0


def test_the_policy_cap_is_plumbed_through_the_round(tmp_path, spec, settings):
    from codeverse.tracks.steps import run_generation_tasks

    agent = _TurnAgent(writes_on=1)
    ctx = _ctx(tmp_path, spec, settings, policy=RoundPolicy(agent_max_turns=15), agent=agent)
    run_generation_tasks(ctx, [GenerationTask(label="baseline", prompt="p", round=0, kind="baseline")])
    assert agent.jobs[0].max_turns == 15
    assert agent.jobs[1].max_turns == RoundPolicy().agent_wrapup_turns  # ... and the landing session


def test_hitting_the_cap_asks_for_a_landing_instead_of_killing_the_session(tmp_ws):
    events = EventLog(tmp_ws.events_path)
    agent = _TurnAgent(writes_on=2)          # session 1 burns its turns and writes nothing
    guard = _guard()
    res = run_agent_task(tmp_ws, agent=agent, task=GenerationTask(label="refine", prompt="do the work", round=1,
                                                                 kind="refine", max_turns=28),
                         budget=guard, events=events, wrapup_turns=5)
    assert len(agent.jobs) == 2 and turn_capped(_result_of(agent, 0))
    wrap = agent.jobs[1]
    assert wrap.max_turns == 5 and "LAST session" in wrap.prompt and "do the work" in wrap.prompt
    assert res.ok and res.turn_capped and res.sessions == 2
    # both sessions are paid for and both are in the guard, under their own labels
    assert res.usage.cost_usd == pytest.approx(0.4) and guard.spent.cost_usd == pytest.approx(0.4)
    assert guard.by_stage["refine"] == pytest.approx(0.4)
    kinds = [e["event"] for e in events.read()]
    assert "generate.turn_cap" in kinds
    done = next(e for e in events.read() if e["event"] == "generate.done")
    assert done["turn_capped"] is True and done["sessions"] == 2


def test_a_crashing_second_session_never_erases_what_the_first_one_spent(tmp_ws):
    class _Flaky(_TurnAgent):
        def run(self, job: AgentJob) -> AgentResult:
            if self.jobs:
                self.jobs.append(job)
                raise RuntimeError("503 storm")
            return super().run(job)

    guard = _guard()
    agent = _Flaky(writes_on=99)
    res = run_agent_task(tmp_ws, agent=agent, task=GenerationTask(label="refine", prompt="p", round=2, kind="refine"),
                         budget=guard)
    assert len(agent.jobs) == 2 and not res.ok
    assert guard.spent.cost_usd == pytest.approx(0.2) and res.usage.cost_usd == pytest.approx(0.2)
    assert "503 storm" in res.notes and guard.by_stage["refine"] == pytest.approx(0.2)


def _result_of(agent, i):
    return AgentResult(ok=False, exit_reason="budget", errors=[f"max_turns ({agent.jobs[i].max_turns}) reached"])


def _guard():
    from codeverse.contracts.common import Budget
    from codeverse.orchestrator import BudgetGuard

    return BudgetGuard(Budget(max_minutes=100))


def test_task_stage_names_the_cost_bucket_a_task_spends_in():
    from codeverse.tracks.generation import task_stage

    def t(kind: str, label: str = "x") -> str:
        return task_stage(GenerationTask(label=label, prompt="p", kind=kind))

    assert t("baseline") == "baseline" and t("refine") == "refine" and t("repair") == "repair"
    assert t("rebuild") == "repair"           # regenerating after a failed build IS repair money
    assert t("zone") == "zones" and t("asset") == "assets" and t("asset_fix") == "assets"
    assert t("compose") == "assemble" and t("env") == "env"
    # the static track's surface-detail round is refine money, not "other" — it filed as
    # Stage.OTHER until 2026-08-30 because _LABEL_STAGES had no "detail" prefix
    assert t("detail") == "refine" and t("detail", "detail_seat") == "refine"
    # an unknown kind falls back to the label, which record_call maps by prefix
    assert t("something_new", "asset_koi") == "asset_koi"


# ----------------------------------------------------------------------------- judge skipping
def _renders():
    from codeverse.contracts.artifacts import RenderSet, RenderView

    return RenderSet(views=[RenderView(name="front", path="x.png")], renderer="fake")


def _gates(errors: int = 0):
    return [GateReport(gate="lint", passed=not errors,
                       findings=[GateFinding(gate="lint", severity=Severity.ERROR, message="e")] * errors)]


def test_skip_judge_reasons_are_only_states_where_the_verdict_is_never_bought(tmp_path, spec, settings):
    from codeverse.tracks.steps import skip_judge_reason

    ctx = _ctx(tmp_path, spec, settings)
    # gate errors are no reason: the gates say what is broken, the verdict says whether
    # the shape is right, and only the verdict can promote the round
    assert skip_judge_reason(ctx, renders=_renders()) == ""
    assert skip_judge_reason(ctx, renders=None) == "no renders"
    # ... the budget/clock stop: the loop's next budget_ok check ends the run, so this
    # verdict cannot promote anything (audit: 2 verdicts / $0.09 bought past the clock)
    ctx.budget.add(Usage(cost_usd=99.0), stage="refine")
    ctx.budget._active_s = (ctx.budget.budget.max_minutes + 1) * 60   # noqa: SLF001
    assert skip_judge_reason(ctx, renders=_renders()) == "budget already exceeded"
    assert StopPolicy(ctx.policy).evaluate([_round(0, 0.5)], budget_ok=ctx.budget.ok()).reason == "budget"
    ctx.judge = None
    assert skip_judge_reason(ctx, renders=_renders()) == "no judge configured"


def test_a_round_that_changed_no_file_never_reaches_the_judge_question(tmp_path, spec, settings):
    from codeverse.tracks.steps import RoundFailed, run_generation_tasks

    class _Idle:
        kind, model = "fake", "m"

        def run(self, job: AgentJob) -> AgentResult:
            return AgentResult(ok=True, exit_reason="completed", files_changed=[], usage=Usage(cost_usd=0.01))

    ctx = _ctx(tmp_path, spec, settings, agent=_Idle(), name="nochange")
    with pytest.raises(RoundFailed):
        run_generation_tasks(ctx, [GenerationTask(label="refine", prompt="p", round=1, kind="refine")])


def test_a_round_with_gate_errors_whose_verdict_was_lost_is_rejudged(tmp_path, spec, settings):
    from codeverse.tracks.steps import rejudge_round

    ctx = _ctx(tmp_path, spec, settings, name="rebuy")
    rec = RoundRecord(index=1, kind="refine", build=BuildResult(ok=True, language="l"),
                      gates=_gates(2), renders=_renders())
    assert rejudge_round(ctx, _pipeline(), rec) is True and len(ctx.judge.calls) == 1


def test_a_round_that_broke_the_gates_does_not_displace_a_clean_one():
    from codeverse.orchestrator import BestSelector

    def r(i, *, build_ok=True, errors=0):
        return RoundRecord(index=i, kind="refine", build=BuildResult(ok=build_ok, language="l"),
                           gates=_gates(errors), commit=f"c{i}")

    assert BestSelector().pick([r(0), r(1, build_ok=False)]) == 0        # broken build never wins
    assert BestSelector().pick([r(0), r(1, errors=4)]) == 0              # clean r00 keeps the crown
    assert BestSelector().pick([r(0, errors=4), r(1)]) == 1
    assert BestSelector().pick([r(0), r(1)]) == 1                        # tie → the later one
    assert BestSelector().pick([r(0, build_ok=False)]) is None


class _Pipeline:
    """The minimum ``RoundPipeline`` the judge path touches."""

    def measure(self, ctx, build): return None
    def gates(self, ctx, i, build, m): return []
    def render(self, ctx, i, build, m): return _renders()
    def plan_summary(self, ctx): return "plan"
    def judge_context(self, ws, plan, i, build, gates): return ""


def _pipeline():
    return _Pipeline()


def _ctx(tmp_path, spec, settings, *, policy: RoundPolicy | None = None, agent=None, name: str = "skip"):
    from codeverse.contracts.common import Budget
    from codeverse.orchestrator import BudgetGuard, RunState
    from codeverse.tracks.common import RunContext

    ws = Workspace(tmp_path / "runs" / name)
    ws.create()
    return RunContext(spec=spec, ws=ws, events=EventLog(ws.events_path), settings=settings,
                      budget=BudgetGuard(Budget(max_minutes=60)), runtime=FakeRuntime(Language.THREEJS),
                      services=FakeServices(), state=RunState(), policy=policy or RoundPolicy(), track=spec.track,
                      rubric="static_object_v1", agent_id="fake:fake-model", judge=FakeJudge(), agent=agent)


# ----------------------------------------------------------------------------- end to end
def _writer(job, ws):
    return {"src/object.js": f"// {job.label} r{job.round}\nexport function build(THREE) {{}}\n"}


def test_the_loop_switches_shape_after_a_regression_and_emits_cost_rounds(tmp_path, chair_plan, settings):
    """r01 regresses hard → r02 is ONE whole-object rewrite, not another per-part fan-out."""
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "regress")
    agent = FakeAgent(_writer, cost=0.02)
    judge = FakeJudge(scores=(0.60, 0.30, 0.35))
    track = StaticObjectTrack(services=FakeServices(contract_errors=2), judge=judge, agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS),
                              policy=RoundPolicy(max_rounds=3, target=0.8, judge_model="gemini:gemini-3.1-pro-preview"))
    rec = track.run(spec, ws)
    assert [r.kind for r in rec.rounds] == ["baseline", "refine", REWRITE_KIND]
    assert rec.extra["stop_reason"] == "regression" and rec.status is RunStatus.PLATEAU
    events = EventLog(ws.events_path).read()
    switch = next(e for e in events if e["event"] == "strategy.switch")
    assert switch["reason"] == "regression"
    rewrite = [j for j in agent.jobs if "rewrite" in j.prompt.lower() or "made the artifact WORSE" in j.prompt]
    assert rewrite, "the rewrite round must tell the agent the last round made it worse"
    assert len([j for j in agent.jobs if j.round == 2]) == 1  # ONE task, not a per-part fan-out
    # every round reports what it burned
    costs = [e for e in events if e["event"] == "cost.round"]
    assert len(costs) == 3 and all("generate" in c["stages"] for c in costs)
    assert costs[0]["agent_turns"] == 0  # the fake agent keeps no transcript
    assert costs[1]["wasted"] is True and costs[1]["waste_reason"] == "regression"
    assert costs[2]["judge_usd"] > 0 and costs[2]["run_usd"] >= costs[1]["run_usd"]
    assert rec.extra["cost_by_stage"]["judge"] > 0 and rec.extra["cost_by_stage"]["refine"] > 0
    # ... and the run opens its own priced ledger (telemetry/cost.jsonl + the root alias) even
    # outside the CLI; the ONE writer is the metered agent/model, so the injected fakes
    # (a bare FakeAgent, a FakeJudge with no chat model) leave it empty — see
    # test_cost_accounting for the rows a metered session writes
    from codeverse.cost.ledger import load_ledger

    assert (ws.root / "cost_ledger.jsonl").is_symlink()  # -> telemetry/cost.jsonl, created on first row
    assert load_ledger(ws.root) == []


def test_a_lint_stuck_run_keeps_every_score_instead_of_deferring_the_verdict(tmp_path, chair_plan, settings):
    def _writer_lint(job, ws):
        return {"src/object.js": f"// {job.label} r{job.round}\nLINT_ERROR\nexport function build(THREE) {{}}\n"}

    spec = make_spec(max_rounds=2)
    ws = Workspace(tmp_path / "runs" / "lintstuck")
    judge = FakeJudge(scores=(0.5, 0.6, 0.7))
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=FakeAgent(_writer_lint),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS),
                              policy=RoundPolicy(max_rounds=2, target=0.9))
    rec = track.run(spec, ws)
    events = EventLog(ws.events_path).read()
    assert [e for e in events if e["event"] == "judge.skipped"] == []
    assert [e for e in events if e["event"] == "judge.retry"] == []
    assert [r.score for r in rec.rounds] == [0.5, 0.6, 0.7]
    assert rec.best_round == 2 and rec.final_score == pytest.approx(0.7)
    # one verdict per round, bought once
    assert len([e for e in events if e["event"] == "judge.done"]) == len(rec.rounds) == len(judge.calls)


def test_a_round_that_raises_still_reports_what_it_burned(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=2, max_minutes=10.0)
    ws = Workspace(tmp_path / "runs" / "cut")

    class _Expensive(FakeAgent):
        def run(self, job):
            res = super().run(job)
            return res.model_copy(update={"usage": Usage(backend="fake", cost_usd=0.04, input_tokens=1000)})

    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5, 0.6)), agent=_Expensive(_writer, minutes=8.0),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS), policy=RoundPolicy(max_rounds=2, target=0.9))
    with fake_clock():
        rec = track.run(spec, ws)
    assert rec.status is RunStatus.BUDGET
    aborted = json.loads((ws.root / "rounds" / "aborted_r01.json").read_text())
    assert aborted["index"] == 1 and aborted["usage"]["cost_usd"] > 0 and "BudgetExceeded" in aborted["notes"]
    assert rec.extra["aborted_rounds"][0]["index"] == 1
    # the aborted round is NOT resumable state, and its money is in the total
    assert [r.index for r in rec.rounds] == [0]
    assert rec.total_usage.cost_usd >= sum(r.usage.cost_usd for r in rec.rounds)
    cut = next(e for e in EventLog(ws.events_path).read() if e["event"] == "cost.round" and e["round"] == 1)
    assert cut["wasted"] is True and cut["waste_reason"] == "aborted" and cut["total_usd"] > 0


def test_fan_out_workers_inherit_the_callers_context():
    import contextvars

    from codeverse.proc import fan_out

    var: contextvars.ContextVar[str] = contextvars.ContextVar("attr", default="process-default")
    var.set("run-42")
    seen = fan_out(range(6), lambda _i: var.get(), max_workers=4, label="attr")
    assert set(seen) == {"run-42"}
    # the caller's own context is untouched by the workers
    assert var.get() == "run-42"


def test_a_run_past_its_hard_ceiling_cannot_start_another_session(tmp_ws):
    from codeverse.contracts.spec import Budget
    from codeverse.orchestrator import BudgetExceeded, BudgetGuard

    g = BudgetGuard(Budget(max_minutes=30.0, max_rounds=4))
    g.start_time -= 36 * 60  # ceiling long crossed, nothing billed along the way
    agent = _TurnAgent(writes_on=1)
    with pytest.raises(BudgetExceeded):
        run_agent_task(tmp_ws, agent=agent,
                       task=GenerationTask(label="baseline", prompt="p", round=0, kind="baseline"),
                       budget=g)
    assert agent.jobs == []  # the treadmill ends BEFORE the agent is invoked


# ----------------------------------------------------------------------------- refine from the best round
def test_a_refine_after_a_regression_restores_the_best_round_and_plans_from_its_verdict(tmp_path):
    """Loop 22 (2026-09-09): 0.66 → 0.61 → 0.47, each refine building on the round before it while
    the best sat in git.  After StopPolicy's ``switch`` the working tree is the BEST round again
    and the tasks come from its verdict; a same-shape refine keeps building on the last round."""
    from types import SimpleNamespace

    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "run").create()
    (ws.src / "scene.js").write_text("// r0: the good one\n")
    c0 = ws.commit("r00")
    (ws.src / "scene.js").write_text("// r1: the regression\n")
    c1 = ws.commit("r01")
    rounds = [_round(0, 0.66), _round(1, 0.47)]
    rounds[0].commit, rounds[1].commit = c0, c1
    events: list[dict] = []
    ctx = SimpleNamespace(ws=ws, state=SimpleNamespace(best_round=0), events=SimpleNamespace(emit=lambda name, **kw: events.append({"event": name, **kw})))
    same = BaseTrack._refine_base(ctx, rounds, "same")
    assert same is rounds[1] and ws.head() == c1 and not events                      # no regression: the last round
    base = BaseTrack._refine_base(ctx, rounds, "switch")
    assert base is rounds[0] and (ws.src / "scene.js").read_text() == "// r0: the good one\n"
    assert ws.head() != c1 and events[0]["event"] == "round.refine_from_best" and events[0]["best"] == 0 and events[0]["regressed"] == 1
    # the best IS the last round (nothing regressed past it): nothing to restore
    ctx.state.best_round = 1
    assert BaseTrack._refine_base(ctx, rounds, "switch") is rounds[1]
