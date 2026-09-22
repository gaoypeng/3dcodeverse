"""The transport-retry lever: a round killed in TRANSPORT (vendor CLI crash, 503
storm, dropped socket) is re-run exactly once before the failure may mean
anything; an agent that ran fine and DECLINED the work is still a plateau.

Shipped after 2026-08-29: rc=247 gemini-cli crashes killed three whole runs at
baseline, and a refine-round crash was delivered as a fake plateau (microscope)."""
from __future__ import annotations

import pytest

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Language, Usage
from codeverse3d.contracts.run import RunStatus
from codeverse3d.proc import EventLog
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.tracks.steps import RoundFailed, looks_quota, looks_transport
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
)
from tests.orchestrator_tracks.test_tracks import _agent_writer as _writer

# --------------------------------------------------------------- signature unit

TRANSPORT = [
    "generate: rc=247; response=<empty>; stderr tail: Error: 503 UNAVAILABLE",
    "refine: rc=1; response=<empty>; stderr tail: at process.processTicksAndRejections",
    "refine: ModelError: Gemini request timed out: The read operation timed out",
    "assembly: rc=3; no result envelope; stderr tail: read ECONNRESET",
    "gen: ApiError: 503 the model is overloaded",
]
VERDICTS = [
    "refine: exit=no_changes",
    "refine: exit=completed; errors=['skipped out-of-root paths: notes.md']",
    "no generation task succeeded",
    "every candidate failed",
    "refine: the agents judged the requested edits unnecessary",
]


QUOTA = [
    "refine: exit=error; errors=[\"You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to "
    "purchase more credits or try again at Sep 14th, 2026 6:25 PM.\"]",
    "generate: RESOURCE_EXHAUSTED: quota exceeded for this project",
    "refine: insufficient_quota: You exceeded your current quota",
]


@pytest.mark.parametrize("msg", TRANSPORT)
def test_transport_signatures_match(msg):
    assert looks_transport(msg)


@pytest.mark.parametrize("msg", QUOTA)
def test_quota_signatures_are_quota_not_transport(msg):
    """cmp8 (2026-09-09): the vendor's usage limit ran out mid-battery and three runs were
    filed as plateau — a retry meets the same wall, and the code did not stop improving."""
    assert looks_quota(msg) and not looks_transport(msg)


@pytest.mark.parametrize("msg", TRANSPORT + VERDICTS)
def test_transport_and_verdict_messages_are_not_quota(msg):
    assert not looks_quota(msg)


@pytest.mark.parametrize("msg", VERDICTS)
def test_agent_verdicts_do_not_match(msg):
    assert not looks_transport(msg)


# --------------------------------------------------------------- track fixtures

CRASH = ["rc=247; response=<empty>; stderr tail: Error: 503 UNAVAILABLE (crash-test)"]


class CrashingAgent(FakeAgent):
    """The first ``crashes`` sessions at ``crash_round`` die like a crashed CLI
    (-1 = every session there dies, forever)."""

    def __init__(self, *, crash_round: int, crashes: int):
        super().__init__(_writer)
        self.crash_round, self.left = crash_round, crashes

    def run(self, job: AgentJob) -> AgentResult:
        if job.round == self.crash_round and self.left != 0:
            self.left -= 1
            self.jobs.append(job)
            return AgentResult(ok=False, exit_reason="error", errors=list(CRASH), usage=Usage(cost_usd=0.01))
        return super().run(job)


class CrashUntilRetried(FakeAgent):
    """Every session at ``crash_round`` dies until the lever's OWN retry event exists
    in the workspace event log — so the whole first attempt crashes (task count never
    assumed) and the whole retry succeeds, with the boundary written by the lever."""

    def __init__(self, *, crash_round: int):
        super().__init__(_writer)
        self.crash_round = crash_round

    def run(self, job: AgentJob) -> AgentResult:
        ws = Workspace(job.workspace)
        retried = any(e["event"] == "round.transport_retry" for e in EventLog(ws.events_path).read())
        if job.round == self.crash_round and not retried:
            self.jobs.append(job)
            return AgentResult(ok=False, exit_reason="error", errors=list(CRASH), usage=Usage(cost_usd=0.01))
        return super().run(job)


class QuotaAfterBaseline(FakeAgent):
    """Baseline works; every refine session dies on the vendor's usage limit."""

    def __init__(self):
        super().__init__(_writer)

    def run(self, job: AgentJob) -> AgentResult:
        if job.round == 0:
            return super().run(job)
        self.jobs.append(job)
        return AgentResult(ok=False, exit_reason="error", errors=[QUOTA[0]], usage=Usage(cost_usd=0.0))


class IdleAfterBaseline(FakeAgent):
    """Baseline works; every refine session completes fine and changes nothing."""

    def __init__(self):
        super().__init__(_writer)

    def run(self, job: AgentJob) -> AgentResult:
        if job.round == 0:
            return super().run(job)
        self.jobs.append(job)
        return AgentResult(ok=True, exit_reason="no_changes", usage=Usage(cost_usd=0.01))


def _track(agent, scores, chair_plan, settings) -> StaticObjectTrack:
    return StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=scores), agent=agent,
                             planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                             settings=settings, runtime=FakeRuntime(Language.THREEJS))


def _events(ws: Workspace) -> list[str]:
    return [e["event"] for e in EventLog(ws.events_path).read()]


# --------------------------------------------------------------- track behaviour

def test_baseline_transport_crash_is_retried_and_the_run_recovers(tmp_path, chair_plan, settings):
    # the ax_umbrella shape: every baseline session dies rc-nonzero with an empty
    # response until the lever steps in; before the lever that was the whole run.
    agent = CrashUntilRetried(crash_round=0)
    ws = Workspace(tmp_path / "runs" / "recover")
    rec = _track(agent, (0.55, 0.7, 0.85), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.PASSED and rec.final_score == pytest.approx(0.85)
    evs = _events(ws)
    assert evs.count("round.transport_retry") == 1 and "run.failed" not in evs
    assert [r.kind for r in rec.rounds] == ["baseline", "refine", "refine"]


def test_refine_transport_crash_retries_only_once_then_plateaus(tmp_path, chair_plan, settings):
    agent = CrashingAgent(crash_round=1, crashes=-1)
    ws = Workspace(tmp_path / "runs" / "plateau")
    rec = _track(agent, (0.55,), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.PLATEAU and rec.extra["stop_reason"] == "plateau"
    assert rec.final_score == pytest.approx(0.55)  # the baseline best is still delivered
    evs = _events(ws)
    assert evs.count("round.transport_retry") == 1  # exactly once, never a loop
    assert "round.no_change" in evs


def test_baseline_transport_crash_is_retried_once_then_fails(tmp_path, chair_plan, settings):
    agent = CrashingAgent(crash_round=0, crashes=-1)
    ws = Workspace(tmp_path / "runs" / "b0")
    with pytest.raises(RoundFailed):
        _track(agent, (0.9,), chair_plan, settings).run(make_spec(max_rounds=2), ws)
    evs = _events(ws)
    assert evs.count("round.transport_retry") == 1 and "run.failed" in evs


def test_an_idle_agent_is_a_plateau_not_a_retry(tmp_path, chair_plan, settings):
    agent = IdleAfterBaseline()
    ws = Workspace(tmp_path / "runs" / "idle")
    rec = _track(agent, (0.55,), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.PLATEAU
    evs = _events(ws)
    assert "round.transport_retry" not in evs and "round.no_change" in evs


def test_a_vendor_usage_limit_stops_the_run_as_agent_quota_not_plateau(tmp_path, chair_plan, settings):
    agent = QuotaAfterBaseline()
    ws = Workspace(tmp_path / "runs" / "quota")
    rec = _track(agent, (0.55,), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.BUDGET and rec.extra["stop_reason"] == "agent_quota"
    assert rec.final_score == pytest.approx(0.55)          # the baseline best is still delivered
    evs = _events(ws)
    assert "round.agent_quota" in evs and "round.transport_retry" not in evs and "round.no_change" not in evs
    assert max(j.round for j in agent.jobs) == 1             # the refine round's sessions met the wall; no retry round
