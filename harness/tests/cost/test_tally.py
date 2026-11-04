"""Tallies: a block's money is its ledger rows, its minutes are its clock minus provider errors."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.instrument import run_ledger
from codeverse3d.cost.ledger import load_ledger, record_call
from codeverse3d.cost.tally import Tally, tally

REQ = ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object:r00:s0")


def test_a_tally_holds_exactly_the_ledger_rows_booked_inside_it(tmp_path: Path):
    u = Usage(backend="gemini", model="gemini-3.8-flash", input_tokens=1_000, output_tokens=10)
    with run_ledger(tmp_path, run="r"), tally() as outer:
        record_call(u, stage="plan")
        with tally() as inner:
            record_call(u, stage="judge")
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
    # both sat out the same storm: the step lost it once, not twice
    assert _side_by_side((600.0, 100.0), (560.0, 100.0)).lost_s() == pytest.approx(100.0)


