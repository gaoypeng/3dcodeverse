"""What the meters saw inside one block of work: its money and its lost time.

A :class:`Tally` is opened with :func:`tally` around a block (a round, a timed step) and
nests; everything metered inside the block books into every tally that is open:

* ``cost.ledger.record_call`` books each priced row, so ``Tally.usage`` is exactly the
  money ``telemetry/cost.jsonl`` holds for the block — the round's cost is read here, never
  accumulated a second way;
* the proxies in ``cost.instrument`` book each API call's and each agent session's wall
  clock and the part of it lost to provider errors (503 / 429 / overloaded / timeout
  retries and their back-off), per thread, so :meth:`Tally.lost_s` knows what the block
  lost even when its work ran side by side.

:func:`timed` turns one block into a ``StepTime`` row — the unit a run's minutes are
summed from (owner, 2026-09-22).  The binding is a ``ContextVar``: ``proc.fan_out`` copies
the context, so a worker thread books into its caller's tallies.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.run import StepTime


class Tally:
    """The money and the lost seconds one block of work booked (see the module docstring)."""

    def __init__(self) -> None:
        self.usage = Usage()
        self._time: dict[int, list[float]] = {}  # thread id → [wall_s, lost_s] of its metered calls
        self._lock = threading.Lock()

    def add_usage(self, usage: Usage) -> None:
        with self._lock:
            self.usage = self.usage + usage

    def add_time(self, wall_s: float, lost_s: float) -> None:
        with self._lock:
            t = self._time.setdefault(threading.get_ident(), [0.0, 0.0])
            t[0] += wall_s
            t[1] += min(lost_s, wall_s)

    def lost_s(self) -> float:
        """Seconds of the block's clock lost to provider errors.  Calls in one thread ran one
        after the other, so their losses add up; threads ran side by side (fan-out sessions,
        judge samples, best-of-N candidates), so the block lost what its slowest thread would
        have been spared: ``max(wall) - max(wall - lost)`` over the threads."""
        with self._lock:
            if not self._time:
                return 0.0
            return max(w for w, _ in self._time.values()) - max(w - lost for w, lost in self._time.values())


_open: ContextVar[tuple[Tally, ...]] = ContextVar("c3d_cost_tallies", default=())


@contextmanager
def tally() -> Iterator[Tally]:
    """Open a tally for the block (nested ones all receive what the block books)."""
    t = Tally()
    token = _open.set(_open.get() + (t,))
    try:
        yield t
    finally:
        _open.reset(token)


def open_tallies() -> tuple[Tally, ...]:
    """The tallies this execution context books into — captured by a caller whose work may
    land later in a thread with no context (a hedge loser's billed round-trip)."""
    return _open.get()


def book_usage(usage: Usage, tallies: tuple[Tally, ...] | None = None) -> None:
    for t in open_tallies() if tallies is None else tallies:
        t.add_usage(usage)


def book_time(wall_s: float, lost_s: float) -> None:
    for t in open_tallies():
        t.add_time(max(0.0, wall_s), max(0.0, lost_s))


@contextmanager
def timed(step: str, into: list[StepTime], *, round_index: int | None = None) -> Iterator[Tally]:
    """Time ``step`` into ``into`` — its wall clock and what it lost — whether the block
    returns or raises.  A step that took no time (a cached stage) leaves no row."""
    t0 = time.monotonic()
    with tally() as t:
        try:
            yield t
        finally:
            wall = time.monotonic() - t0
            if wall >= 0.01:
                into.append(StepTime(step=step, round=round_index, wall_s=round(wall, 2),
                                     lost_s=round(min(wall, t.lost_s()), 2)))


__all__ = ["Tally", "book_time", "book_usage", "open_tallies", "tally", "timed"]
