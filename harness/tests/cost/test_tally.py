"""Tallies: a block's money is its ledger rows, its minutes are its clock minus provider errors."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ChatResponse
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.run import StepTime
from codeverse3d.cost.context import AttemptRecord, attempt_sink
from codeverse3d.cost.instrument import MeteredAgent, MeteredChatModel, run_ledger
from codeverse3d.cost.ledger import load_ledger, record_call
from codeverse3d.cost.tally import Tally, tally, timed

REQ = ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object_v1:r00:s0")


def test_a_tally_holds_exactly_the_ledger_rows_booked_inside_it(tmp_path: Path):
    u = Usage(backend="gemini", model="gemini-3.7-flash", input_tokens=1_000, output_tokens=10)
    with run_ledger(tmp_path, run="r"), tally() as outer:
        record_call(u, stage="plan")
        with tally() as inner:
            record_call(u, stage="judge")
            record_call(u, stage="judge", source="attempt")  # forensics: its call's row carries it
    assert inner.usage.input_tokens == 1_000 and outer.usage.input_tokens == 2_000
    assert outer.usage.cost_usd == pytest.approx(sum(r.cost_usd for r in load_ledger(tmp_path)))


def _side_by_side(*calls: tuple[float, float]) -> Tally:
    """Book one (wall, lost) per thread, every thread alive at once (idents are reused after exit)."""
    t, gate = Tally(), threading.Barrier(len(calls))

    def worker(wall: float, lost: float) -> None:
        gate.wait()
        t.add_time(wall, lost)
        gate.wait()

    threads = [threading.Thread(target=worker, args=c) for c in calls]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return t


def test_lost_time_adds_up_in_a_thread_and_takes_the_slowest_thread_side_by_side():
    t = Tally()
    t.add_time(60.0, 10.0)
    t.add_time(30.0, 5.0)  # one thread, one call after the other
    assert t.lost_s() == pytest.approx(15.0)
    # A lost 100 of its 600 s; B took 550 without an error: the step would have ended at 550
    assert _side_by_side((600.0, 100.0), (550.0, 0.0)).lost_s() == pytest.approx(50.0)
    # B took 600 anyway: A's errors cost the step nothing
    assert _side_by_side((600.0, 100.0), (600.0, 0.0)).lost_s() == pytest.approx(0.0)
    # both sat out the same storm: the step lost it once, not twice
    assert _side_by_side((600.0, 100.0), (560.0, 100.0)).lost_s() == pytest.approx(100.0)


def test_a_timed_step_is_one_row_with_its_wall_and_what_it_lost():
    steps: list[StepTime] = []
    with timed("judge", steps, round_index=2) as t:
        t.add_time(5.0, 2.0)
        time.sleep(0.03)
    (row,) = steps
    assert row.step == "judge" and row.round == 2 and row.wall_s >= 0.03
    assert row.lost_s == pytest.approx(row.wall_s), "lost is clipped to the step's own clock"
    with timed("skeleton", steps):
        pass
    assert len(steps) == 1, "a step that took no time leaves no row"
    with pytest.raises(RuntimeError), timed("build", steps):
        time.sleep(0.02)
        raise RuntimeError("the time was still spent")
    assert steps[-1].step == "build"


class SinkChat:
    """A backend that reports its round-trips like ``GeminiModel`` does."""

    provider, model, id = "gemini", "gemini-3.7-flash", "gemini:gemini-3.7-flash"

    def __init__(self, trips: list[tuple[str, int, float]]):
        self.trips = trips  # (outcome, answered latency ms, seconds the round-trip holds)

    def generate(self, request: ChatRequest) -> ChatResponse:
        sink = attempt_sink()
        last = len(self.trips)
        for i, (outcome, latency, hold) in enumerate(self.trips, 1):
            time.sleep(hold)
            sink(AttemptRecord(attempt=i, key="k", outcome=outcome, discarded=i != last,
                               usage=Usage(model=self.model, latency_ms=latency)))
        return ChatResponse(text="ok", usage=Usage(model=self.model, input_tokens=10, latency_ms=self.trips[-1][1]))


def _lost(chat: object, tmp_path: Path) -> tuple[float, float]:
    with run_ledger(tmp_path, run="r"), tally() as t:
        MeteredChatModel(chat).generate(REQ)
    (wall, lost), = t._time.values()
    return wall, lost


def test_a_clean_call_loses_nothing_and_a_503_before_the_answer_is_lost(tmp_path: Path):
    wall, lost = _lost(SinkChat([("ok", 50, 0.05)]), tmp_path)
    assert lost == 0.0 and wall >= 0.05
    wall, lost = _lost(SinkChat([("5xx", 0, 0.10), ("ok", 40, 0.04)]), tmp_path)
    assert lost == pytest.approx(wall - 0.040, abs=0.02) and lost >= 0.09
    # a reply that came back unusable is the model's own time, not the provider's
    wall, lost = _lost(SinkChat([("skip", 60, 0.06), ("ok", 40, 0.04)]), tmp_path)
    assert lost == 0.0


def test_an_agent_session_books_what_its_cli_lost_to_the_provider(tmp_path: Path):
    class Cli:
        kind, model, id = "gemini-cli", "gemini-3.7-flash", "gemini-cli:gemini-3.7-flash"

        def run(self, job: AgentJob) -> AgentResult:
            time.sleep(0.02)
            return AgentResult(ok=True, provider_wait_s=0.015, usage=Usage(backend="gemini-cli", input_tokens=5))

    with run_ledger(tmp_path, run="r"), tally() as t:
        MeteredAgent(Cli()).run(AgentJob(workspace=str(tmp_path), prompt="p", label="baseline", round=0))
    (wall, lost), = t._time.values()
    assert wall >= 0.02 and lost == pytest.approx(0.015)
    assert t.usage.input_tokens == 5, "the session row is booked into the tally like any ledger row"
