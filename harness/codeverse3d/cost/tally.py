"""What the meters saw inside one block of work: its money and its lost time.

A :class:`Tally` opened by :func:`tally` nests; the ledger books priced rows and the
``cost.instrument`` proxies book wall/lost seconds into every open tally.  :func:`timed`
turns a block into a ``StepTime`` row.  The binding is a ``ContextVar`` that fan-out copies.
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
        """Seconds lost to provider errors: losses add within a thread; across parallel threads
        it is ``max(wall) - max(wall - lost)``."""
        with self._lock:
            if not self._time:
                return 0.0
            return max(w for w, _ in self._time.values()) - max(w - lost for w, lost in self._time.values())


_open: ContextVar[tuple[Tally, ...]] = ContextVar("c3d_cost_tallies", default=())


@contextmanager
def tally() -> Iterator[Tally]:
    t = Tally()
    token = _open.set(_open.get() + (t,))
    try:
        yield t
    finally:
        _open.reset(token)


def open_tallies() -> tuple[Tally, ...]:
    """The tallies this context books into, for callers whose work lands later in a thread without it."""
    return _open.get()


def book_usage(usage: Usage, tallies: tuple[Tally, ...] | None = None) -> None:
    for t in open_tallies() if tallies is None else tallies:
        t.add_usage(usage)


def book_time(wall_s: float, lost_s: float) -> None:
    for t in open_tallies():
        t.add_time(max(0.0, wall_s), max(0.0, lost_s))


@contextmanager
def timed(step: str, into: list[StepTime], *, round_index: int | None = None) -> Iterator[Tally]:
    """Time ``step`` into ``into`` whether the block returns or raises; a ~0 s step leaves no row."""
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
