"""The transport-retry lever: a round killed by the PROVIDER (a 503 storm, a dropped
socket, an overloaded API) is re-run exactly once before the failure may mean anything;
an agent that ran fine and DECLINED the work is still a no_change stop; the vendor's
spent usage limit stops the run as agent_quota.

Shipped after 2026-08-29: rc=247 gemini-cli crashes killed three whole runs at
baseline, and a refine-round crash was delivered as a fake plateau (microscope).
Typed since 2026-09-22: each backend classifies its own CLI's failure
(``AgentResult.transient`` / ``.quota``), the flags ride ``GenerationResult`` into
``RoundFailed``, and the loop reads them — it matched substrings of the joined
notes before, with a vocabulary that had drifted from the backends'."""
from __future__ import annotations

import pytest

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Language, Usage
from codeverse3d.contracts.run import RunStatus
from codeverse3d.models.base import ModelError
from codeverse3d.proc import EventLog
from codeverse3d.tracks.generation import GenerationResult
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.tracks.steps import RoundFailed
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

# --------------------------------------------------------------- the typed failure

def test_round_failed_carries_the_flags_of_its_tasks_never_their_words():
    ok = GenerationResult(ok=False, notes="exit=completed; errors=['503 in a code comment']", label="a")
    storm = GenerationResult(ok=False, notes="exit=error", label="b", transient=True)
    spent = GenerationResult(ok=False, notes="exit=error", label="c", quota=True)
    assert not RoundFailed.of([ok], "x").transient      # a word in the notes is only a word
    e = RoundFailed.of([ok, storm], "x")
    assert e.transient and not e.quota and str(e) == "a: exit=completed; errors=['503 in a code comment']; b: exit=error"
    assert RoundFailed.of([ok, spent], "x").quota
    assert str(RoundFailed.of([], "no generation task succeeded")) == "no generation task succeeded"


def test_a_raised_error_is_classified_by_its_type():
    assert GenerationResult.from_error("g", ModelError("Gemini request timed out", retryable=True, status=408)).transient
    assert GenerationResult.from_error("g", ModelError("Gemini API error 503", status=503)).transient
    assert GenerationResult.from_error("g", ModelError("overloaded", status=529)).transient
    assert not GenerationResult.from_error("g", ModelError("bad request", status=400)).transient
    assert not GenerationResult.from_error("g", ValueError("503 in the message")).transient


# --------------------------------------------------------------- track fixtures

CRASH = ["rc=247; response=<empty>; stderr tail: Error: 503 UNAVAILABLE (crash-test)"]
QUOTA = "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits."


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
            return AgentResult(ok=False, exit_reason="error", errors=list(CRASH), usage=Usage(cost_usd=0.01),
                               transient=True)
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
            return AgentResult(ok=False, exit_reason="error", errors=list(CRASH), usage=Usage(cost_usd=0.01),
                               transient=True)
        return super().run(job)


def after_baseline(failure: AgentResult) -> FakeAgent:
    """Baseline works; every refine session ends in ``failure``."""
    return FakeAgent(lambda job, ws: _writer(job, ws) if job.round == 0 else failure)


QUOTA_WALL = AgentResult(ok=False, exit_reason="error", errors=[QUOTA], usage=Usage(cost_usd=0.0), quota=True)
IDLE = AgentResult(ok=True, exit_reason="no_changes", usage=Usage(cost_usd=0.01))
# a transport-looking message its backend did NOT classify transient (a CLI crash, no provider error)
UNTYPED_CRASH = AgentResult(ok=False, exit_reason="error", usage=Usage(cost_usd=0.01),
                            errors=["rc=1; response=<empty>; stderr tail: at process.processTicksAndRejections"])


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
    assert rec.status is RunStatus.MAX_ROUNDS and [r.score for r in rec.rounds] == pytest.approx([0.55, 0.7, 0.85, 0.85])
    evs = _events(ws)
    assert evs.count("round.transport_retry") == 1 and "run.failed" not in evs
    assert [r.kind for r in rec.rounds] == ["baseline", "refine", "refine", "refine"]


def test_refine_transport_crash_retries_only_once_then_stops_as_no_change(tmp_path, chair_plan, settings):
    agent = CrashingAgent(crash_round=1, crashes=-1)
    ws = Workspace(tmp_path / "runs" / "plateau")
    rec = _track(agent, (0.55,), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.NO_CHANGE and rec.extra["stop_reason"] == "no_change"
    assert [r.score for r in rec.rounds] == pytest.approx([0.55])  # the baseline is still recorded
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


@pytest.mark.parametrize("refine", [UNTYPED_CRASH, IDLE], ids=["untyped_crash", "idle"])
def test_an_untyped_crash_or_an_idle_session_is_a_no_change_stop_not_a_retry(tmp_path, chair_plan, settings, refine):
    """The words "response=<empty>" used to buy a retry of the whole round; only the backend's
    own verdict does now (0 of 34 recorded gemini-cli error exits lacked a provider signature)."""
    ws = Workspace(tmp_path / "runs" / "crash")
    rec = _track(after_baseline(refine), (0.55,), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.NO_CHANGE
    evs = _events(ws)
    assert "round.transport_retry" not in evs and "round.no_change" in evs


def test_a_vendor_usage_limit_stops_the_run_as_agent_quota_not_no_change(tmp_path, chair_plan, settings):
    agent = after_baseline(QUOTA_WALL)
    ws = Workspace(tmp_path / "runs" / "quota")
    rec = _track(agent, (0.55,), chair_plan, settings).run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.AGENT_QUOTA and rec.extra["stop_reason"] == "agent_quota"
    assert [r.score for r in rec.rounds] == pytest.approx([0.55])  # the baseline is still recorded
    evs = _events(ws)
    assert "round.agent_quota" in evs and "round.transport_retry" not in evs and "round.no_change" not in evs
    assert max(j.round for j in agent.jobs) == 1             # the refine round's sessions met the wall; no retry round
