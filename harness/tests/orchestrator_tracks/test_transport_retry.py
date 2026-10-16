"""The transport-retry lever: a round killed by the PROVIDER (typed ``transient``) is re-run
exactly once; a declined round is a no_change stop; a spent vendor quota stops as agent_quota."""
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

def test_round_failed_carries_the_typed_flags_of_its_tasks_never_their_words():
    ok = GenerationResult(ok=False, notes="exit=completed; errors=['503 in a code comment']", label="a")
    storm = GenerationResult(ok=False, notes="exit=error", label="b", transient=True)
    spent = GenerationResult(ok=False, notes="exit=error", label="c", quota=True)
    assert not RoundFailed.of([ok], "x").transient      # a word in the notes is only a word
    e = RoundFailed.of([ok, storm], "x")
    assert e.transient and not e.quota and str(e) == "a: exit=completed; errors=['503 in a code comment']; b: exit=error"
    assert RoundFailed.of([ok, spent], "x").quota
    assert str(RoundFailed.of([], "no generation task succeeded")) == "no generation task succeeded"
    # a raised error is classified by its type
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


def test_baseline_transport_crash_is_retried_once_then_fails(tmp_path, chair_plan, settings):
    agent = CrashingAgent(crash_round=0, crashes=-1)
    ws = Workspace(tmp_path / "runs" / "b0")
    with pytest.raises(RoundFailed):
        _track(agent, (0.9,), chair_plan, settings).run(make_spec(max_rounds=2), ws)
    evs = _events(ws)
    assert evs.count("round.transport_retry") == 1 and "run.failed" in evs


@pytest.mark.parametrize("refine", [UNTYPED_CRASH, IDLE], ids=["untyped_crash", "idle"])
def test_an_untyped_crash_or_an_idle_session_is_a_no_change_stop_not_a_retry(tmp_path, chair_plan, settings, refine):
    """Only the backend's typed verdict buys a retry, never words in the notes."""
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


# --------------------------------------------------------------- D112: one rule for a hung session, a judge reserve,
# and the edits the clock stopped are judged
HUNG = AgentResult(ok=False, exit_reason="timeout", usage=Usage(cost_usd=0.01), stalled=True,
                   errors=["killed by watchdog (idle) after 901s"])
WORKED_TO_THE_LIMIT = AgentResult(ok=False, exit_reason="timeout", usage=Usage(cost_usd=0.01),
                                  errors=["killed by watchdog (hard_timeout) after 1200s"])


def test_a_hung_refine_session_is_retried_once_whatever_the_vendor_log_said(tmp_path, chair_plan, settings):
    """Iteration 5: the same 901 s / zero-file kill was retried in r1_05 / r1_09 (a stray 4 s 503 in agy's
    log) and ended r1_07 / r1_08 as no_change.  Now a hang is ``stalled`` and retried by the clock alone."""
    calls = {"n": 0}

    def writer(job, ws):
        if job.round == 1:
            calls["n"] += 1
            if calls["n"] == 1:
                return HUNG
        return _writer(job, ws)

    ws = Workspace(tmp_path / "runs" / "hung")
    rec = _track(FakeAgent(writer), (0.55, 0.7), chair_plan, settings).run(make_spec(max_rounds=1), ws)
    retries = [e for e in EventLog(ws.events_path).read() if e["event"] == "round.transport_retry"]
    assert len(retries) == 1 and retries[0]["stalled"] is True
    assert [r.score for r in rec.rounds] == pytest.approx([0.55, 0.7])


def test_a_session_that_worked_to_its_hard_limit_is_not_retried(tmp_path, chair_plan, settings):
    ws = Workspace(tmp_path / "runs" / "limit")
    rec = _track(after_baseline(WORKED_TO_THE_LIMIT), (0.55,), chair_plan, settings).run(make_spec(max_rounds=2), ws)
    assert rec.status is RunStatus.NO_CHANGE and "round.transport_retry" not in _events(ws)


def test_no_refine_round_is_launched_that_could_not_also_be_judged(tmp_path, chair_plan, settings):
    """The baseline burns the clock down to less than a minimum window + the judge reserve (7 min)."""
    from tests.orchestrator_tracks.conftest import fake_clock

    agent = FakeAgent(_writer, minutes=4.0)
    ws = Workspace(tmp_path / "runs" / "reserve")
    with fake_clock():
        rec = _track(agent, (0.55,), chair_plan, settings).run(make_spec(max_rounds=3, max_minutes=10.0), ws)
    assert rec.status is RunStatus.BUDGET and [j.round for j in agent.jobs] == [0]
    skipped = [e for e in EventLog(ws.events_path).read() if e["event"] == "round.skipped_budget"]
    assert len(skipped) == 1 and skipped[0]["need_s"] == 420.0 and skipped[0]["left_s"] == pytest.approx(360.0)


def test_a_refine_session_leaves_the_judge_reserve_on_the_clock(tmp_path, chair_plan, settings):
    import time

    left: dict[int, float] = {}

    def writer(job, ws):
        left[job.round] = job.hard_deadline_s - time.monotonic()
        return _writer(job, ws)

    ws = Workspace(tmp_path / "runs" / "deadline")
    _track(FakeAgent(writer), (0.55, 0.6), chair_plan, settings).run(make_spec(max_rounds=1, max_minutes=10.0), ws)
    assert 560 < left[0] <= 600, "the baseline is not a refine: no reserve"
    assert 320 < left[1] <= 360, "a refine session's deadline ends judge_reserve_s (240 s) before the budget"


def test_refine_edits_the_clock_stopped_are_built_and_judged(tmp_path, chair_plan, settings):
    """Iteration 5, r1_09: the retry's edits were committed, then the budget stop threw them away and
    finalise rebuilt round 0.  Now ONE bounded grace builds, renders and judges them."""
    from tests.orchestrator_tracks.conftest import FAKE_CLOCK, fake_clock

    def writer(job, ws):
        if job.round == 1:
            FAKE_CLOCK["minutes"] = 10.5      # the session ran to the run's edge
        return _writer(job, ws)

    ws = Workspace(tmp_path / "runs" / "salvage")
    with fake_clock():
        rec = _track(FakeAgent(writer), (0.6, 0.8), chair_plan, settings).run(make_spec(max_rounds=3, max_minutes=10.0), ws)
    assert rec.status is RunStatus.BUDGET
    assert [r.score for r in rec.rounds] == pytest.approx([0.6, 0.8])
    assert rec.rounds[1].kind == "refine" and "salvaged" in rec.rounds[1].notes
    salvage = [e for e in EventLog(ws.events_path).read() if e["event"] == "budget.salvage"]
    assert len(salvage) == 1 and "src/object.js" in salvage[0]["files"]


def test_a_stop_before_any_refine_edit_salvages_nothing(tmp_path, chair_plan, settings):
    from tests.orchestrator_tracks.conftest import FAKE_CLOCK, fake_clock

    def writer(job, ws):
        if job.round == 1:
            FAKE_CLOCK["minutes"] = 10.5
            return WORKED_TO_THE_LIMIT
        return _writer(job, ws)

    ws = Workspace(tmp_path / "runs" / "nothing")
    with fake_clock():
        rec = _track(FakeAgent(writer), (0.6,), chair_plan, settings).run(make_spec(max_rounds=3, max_minutes=10.0), ws)
    assert rec.status is RunStatus.BUDGET and len(rec.rounds) == 1 and "budget.salvage" not in _events(ws)


# --------------------------------------------------------------- D112 review: the hang rule on every path
def test_a_split_round_is_stalled_only_when_every_session_hung():
    """The round loop retries a round WHOLE: one session that worked to its hard limit and wrote
    nothing must not be re-run because its sibling hung (D112's rule for the hard-limit kill)."""
    hung = GenerationResult(ok=False, notes="killed by watchdog (idle)", label="a", stalled=True)
    worked = GenerationResult(ok=False, notes="killed by watchdog (hard_timeout)", label="b")
    storm = GenerationResult(ok=False, notes="exit=error", label="c", transient=True)
    assert RoundFailed.of([hung], "x").stalled
    assert RoundFailed.of([hung, hung.model_copy(update={"label": "d"})], "x").stalled
    assert RoundFailed.of([hung, storm], "x").stalled            # every session died without working
    assert not RoundFailed.of([hung, worked], "x").stalled
    assert not RoundFailed.of([worked], "x").stalled


def test_hung_best_of_n_candidates_get_the_round_loops_retry(tmp_path, chair_plan, settings):
    """``--candidates 2``: every candidate's session hangs.  The round's failure must carry ``stalled``
    like a single-candidate round's does, so round 0 gets its one retry before the run fails."""
    agent = FakeAgent(lambda job, ws: HUNG)
    ws = Workspace(tmp_path / "runs" / "hung_n")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=agent,
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS), n_candidates=2)
    with pytest.raises(RoundFailed) as err:
        track.run(make_spec(max_rounds=1), ws)
    assert err.value.stalled
    retries = [e for e in EventLog(ws.events_path).read() if e["event"] == "round.transport_retry"]
    assert len(retries) == 1 and retries[0]["stalled"] is True


def test_a_single_shot_refine_needs_only_the_judge_reserve(tmp_path, chair_plan, settings):
    """The economy profile's refine is one API call, not a session: 270 s left (> the 240 s judge
    reserve, < the 420 s an agent session needs) still opens the round and gets it judged."""
    from tests.orchestrator_tracks.conftest import FAKE_CLOCK, fake_clock

    calls = {"n": 0}

    def respond(req):
        calls["n"] += 1
        if calls["n"] == 1:
            FAKE_CLOCK["minutes"] = 5.5          # the baseline call left 270 s of the 10-min clock
        return (f"=== FILE: src/object.js ===\n// call {calls['n']}\n"
                "export function build(THREE) { return new THREE.Group(); }\n=== END FILE ===")

    ws = Workspace(tmp_path / "runs" / "economy")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.55, 0.7)), model=FakeChatModel(respond),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    with fake_clock():
        rec = track.run(make_spec(generator="single-shot:gemini:x", max_rounds=1, max_minutes=10.0), ws)
    assert "round.skipped_budget" not in _events(ws)
    assert [r.kind for r in rec.rounds] == ["baseline", "refine"]
    assert [r.score for r in rec.rounds] == pytest.approx([0.55, 0.7])
