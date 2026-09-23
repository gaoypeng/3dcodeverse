"""Session limits, judge skipping, and cost attribution of a round."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Language, Usage
from codeverse3d.contracts.run import RunStatus
from codeverse3d.cost.instrument import MeteredAgent, run_ledger
from codeverse3d.cost.ledger import load_ledger
from codeverse3d.orchestrator import RoundPolicy
from codeverse3d.proc import EventLog
from codeverse3d.tracks.generation import (
    DEFAULT_WRAPUP_TURNS,
    GenerationTask,
    run_agent_task,
    turn_capped,
)
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


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


def test_hitting_the_cap_asks_for_a_landing_instead_of_killing_the_session(tmp_ws, monkeypatch):
    monkeypatch.setenv("C3D_AGENT_MAX_TURNS", "28")
    events = EventLog(tmp_ws.events_path)
    agent = _TurnAgent(writes_on=2)          # session 1 burns its turns and writes nothing
    with run_ledger(tmp_ws.root):
        res = run_agent_task(tmp_ws, agent=MeteredAgent(agent),
                             task=GenerationTask(label="refine", prompt="do the work", round=1, kind="refine"),
                             budget=_guard(), events=events)
    assert len(agent.jobs) == 2 and turn_capped(_result_of(agent, 0)) and agent.jobs[0].max_turns == 28
    wrap = agent.jobs[1]
    assert wrap.max_turns == DEFAULT_WRAPUP_TURNS and "LAST session" in wrap.prompt and "do the work" in wrap.prompt
    assert res.ok and res.turn_capped and res.sessions == 2
    # both sessions are paid for and each is a ledger row of its own
    assert res.usage.cost_usd == pytest.approx(0.4)
    rows = load_ledger(tmp_ws.root)
    assert len(rows) == 2 and all(r.stage == "refine" and r.cost_usd == pytest.approx(0.2) for r in rows)
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

    agent = _Flaky(writes_on=99)
    with run_ledger(tmp_ws.root):
        res = run_agent_task(tmp_ws, agent=MeteredAgent(agent),
                             task=GenerationTask(label="refine", prompt="p", round=2, kind="refine"), budget=_guard())
    assert len(agent.jobs) == 2 and not res.ok and "503 storm" in res.notes
    (row,) = load_ledger(tmp_ws.root)  # the first session's row stands
    assert res.usage.cost_usd == pytest.approx(0.2) and row.cost_usd == pytest.approx(0.2) and row.round == 2


def _result_of(agent, i):
    return AgentResult(ok=False, exit_reason="budget", errors=[f"max_turns ({agent.jobs[i].max_turns}) reached"])


def _guard():
    from codeverse3d.contracts.common import Budget
    from codeverse3d.orchestrator import BudgetGuard

    return BudgetGuard(Budget(max_minutes=100))


def test_a_task_kind_names_the_stage_its_session_row_is_filed_under():
    from codeverse3d.cost.types import stage_for_label as t  # what MeteredAgent files a session by

    assert t("baseline") == "baseline" and t("refine") == "refine" and t("repair") == "repair"
    assert t("rebuild") == "repair"           # regenerating after a failed build IS repair money
    assert t("zone") == "zones" and t("asset") == "assets" and t("asset_fix") == "assets"
    assert t("env") == "env"
    # the static track's surface-detail round (gone 2026-09-22) was refine money, not "other":
    # the recorded runs that have one still file it there
    assert t("detail") == "refine" and t("detail_seat") == "refine"


# ----------------------------------------------------------------------------- judge skipping
def _renders():
    from codeverse3d.contracts.artifacts import RenderSet, RenderView

    return RenderSet(views=[RenderView(name="front", path="x.png")], renderer="fake")


def test_skip_judge_reasons_are_only_states_where_the_verdict_is_never_bought(tmp_path, spec, settings):
    from codeverse3d.tracks.steps import skip_judge_reason

    ctx = _ctx(tmp_path, spec, settings)
    # gate errors are no reason: the gates say what is broken, the verdict says whether
    # the shape is right, and only the verdict lets a pick choose the round
    assert skip_judge_reason(ctx, renders=_renders()) == ""
    assert skip_judge_reason(ctx, renders=None) == "no renders"
    # a round that finished past the clock is still judged: its generation is paid for and
    # an unjudged round can never be picked (2026-09-22)
    ctx.budget._active_s = (ctx.budget.budget.max_minutes + 1) * 60   # noqa: SLF001
    assert not ctx.budget.ok() and skip_judge_reason(ctx, renders=_renders()) == ""
    ctx.judge = None
    assert skip_judge_reason(ctx, renders=_renders()) == "no judge configured"


def test_a_round_that_changed_no_file_never_reaches_the_judge_question(tmp_path, spec, settings):
    from codeverse3d.tracks.steps import RoundFailed, run_generation_tasks

    idle = FakeAgent(lambda job, ws: AgentResult(ok=True, exit_reason="completed", files_changed=[], usage=Usage(cost_usd=0.01)))
    ctx = _ctx(tmp_path, spec, settings, agent=idle, name="nochange")
    with pytest.raises(RoundFailed):
        run_generation_tasks(ctx, [GenerationTask(label="refine", prompt="p", round=1, kind="refine")])


def _ctx(tmp_path, spec, settings, *, policy: RoundPolicy | None = None, agent=None, name: str = "skip"):
    from codeverse3d.contracts.common import Budget
    from codeverse3d.orchestrator import BudgetGuard, RunState
    from codeverse3d.tracks.common import RunContext

    ws = Workspace(tmp_path / "runs" / name)
    ws.create()
    return RunContext(spec=spec, ws=ws, events=EventLog(ws.events_path), settings=settings,
                      budget=BudgetGuard(Budget(max_minutes=60)), runtime=FakeRuntime(Language.THREEJS),
                      services=FakeServices(), state=RunState(), policy=policy or RoundPolicy(), track=spec.track,
                      rubric="static_object_v1", agent_id="fake:fake-model", judge=FakeJudge(), agent=agent)


# ----------------------------------------------------------------------------- end to end
def _writer(job, ws):
    return {"src/object.js": f"// {job.label} r{job.round}\nexport function build(THREE) {{}}\n"}


def test_a_regression_changes_nothing_about_the_loop_and_every_round_reports_its_cost(tmp_path, chair_plan, settings):
    """A regression changes nothing about the loop; the run's total is its ledger's (D84)."""
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "regress")
    agent = FakeAgent(_writer, cost=0.02)
    judge = FakeJudge(scores=(0.60, 0.30, 0.35, 0.40))
    track = StaticObjectTrack(services=FakeServices(contract_errors=2), judge=judge, agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS), policy=RoundPolicy(max_rounds=3))
    rec = track.run(spec, ws)
    assert [r.kind for r in rec.rounds] == ["baseline", "refine", "refine", "refine"]
    assert rec.extra["stop_reason"] == "max_rounds" and rec.status is RunStatus.MAX_ROUNDS
    # every round's cost is the ledger rows it booked — the injected agent and planner are
    # metered like the ones services hand out, and the fake judge bills a row per verdict as a
    # real one's model does — and the run's total is its ledger's, the planner included, with
    # no per-stage copy on the record
    assert all(r.usage.cost_usd >= 0.02 + 0.003 for r in rec.rounds)
    rows = load_ledger(ws.root)
    assert {r.stage for r in rows} == {"plan", "baseline", "refine", "judge"}
    assert rec.total_usage.cost_usd == pytest.approx(sum(r.cost_usd for r in rows))
    assert rec.total_usage.cost_usd > sum(r.usage.cost_usd for r in rec.rounds)
    assert "cost_by_stage" not in rec.extra


def test_fan_out_workers_inherit_the_callers_context():
    import contextvars

    from codeverse3d.proc import fan_out

    var: contextvars.ContextVar[str] = contextvars.ContextVar("attr", default="process-default")
    var.set("run-42")
    seen = fan_out(range(6), lambda _i: var.get(), max_workers=4, label="attr")
    assert set(seen) == {"run-42"}
    # the caller's own context is untouched by the workers
    assert var.get() == "run-42"


def test_a_run_past_its_hard_ceiling_cannot_start_another_session(tmp_ws):
    from codeverse3d.contracts.spec import Budget
    from codeverse3d.orchestrator import BudgetExceeded, BudgetGuard

    g = BudgetGuard(Budget(max_minutes=30.0, max_rounds=4))
    g.start_time -= 36 * 60  # ceiling long crossed, nothing billed along the way
    agent = _TurnAgent(writes_on=1)
    with pytest.raises(BudgetExceeded):
        run_agent_task(tmp_ws, agent=agent,
                       task=GenerationTask(label="baseline", prompt="p", round=0, kind="baseline"),
                       budget=g)
    assert agent.jobs == []  # the treadmill ends BEFORE the agent is invoked
