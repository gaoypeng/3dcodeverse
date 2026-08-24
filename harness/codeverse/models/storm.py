"""Process-wide "this model is out of capacity" signal (HTTP 503 / 529).

A 429 is per key, so the :class:`~codeverse.models.keypool.KeyPool` cures it by
rotating.  A **capacity storm** is not: Gemini's *"This model is currently
experiencing high demand"* comes back on every key at once, so rotation buys
nothing and each worker that discovers the storm independently pays a full
failed round-trip to learn what its 29 siblings already know.

Measured on this box (``docs/COST.md`` Part III): 2 833 storm waits across the
recorded batteries, 17.7 h of worker time asleep — 22 – 39 % of every battery's
wall clock — plus one wasted round-trip per wait.

``StormGate`` turns that into ONE shared wait:

* the first worker to see a 503 calls :meth:`hit`, which closes the gate for a
  short, escalating window (never longer than ``max_wait_s`` — the house rule is
  that patience comes from the number of waits, not the length of one);
* every other worker parks in :meth:`enter` instead of issuing a call that is
  almost certain to fail;
* once the window elapses exactly **one** worker at a time is let through as a
  probe (a lease, so a crashed prober cannot wedge the gate).  Its success
  (:meth:`ok`) reopens the gate for everybody.

The gate never *adds* waiting: when no storm is running :meth:`enter` returns
immediately without taking a lock hop that matters.  ``clock`` / ``sleep`` are
injectable so tests run on a fake clock.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from codeverse.models.keypool import MAX_WAIT_S


class StormGate:
    """Shared 503 back-pressure for one model.  See the module docstring."""

    def __init__(
        self,
        name: str = "model",
        *,
        base_delay: float = 1.0,
        max_wait_s: float = MAX_WAIT_S,
        probe_lease_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self._base = float(base_delay)
        self._max_wait = float(max_wait_s)
        self._probe_lease = float(probe_lease_s)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._storm = False
        self._closed_until = 0.0
        self._probe_until = 0.0
        self._streak = 0
        self.n_hits = 0
        self.n_probes = 0
        self.n_storms = 0
        self.parked_s = 0.0

    # ------------------------------------------------------------------ public
    @property
    def storming(self) -> bool:
        return self._storm

    def enter(self) -> float:
        """Block until this thread may issue a call; returns the seconds waited."""
        t0 = self._clock()
        while True:
            with self._lock:
                now = self._clock()
                if not self._storm:
                    return now - t0
                if now < self._closed_until:
                    wait = min(self._max_wait, self._closed_until - now)
                elif now >= self._probe_until:
                    # the window elapsed and no probe is in flight: this thread is it
                    self._probe_until = now + self._probe_lease
                    self._n_probe_hit()
                    return now - t0
                else:
                    wait = min(self._max_wait, self._probe_until - now)
                wait = max(0.01, wait)
                self.parked_s += wait
            self._sleep(wait)

    def hit(self, retry_after_s: float | None = None) -> float:
        """Record a capacity 503/529.  Returns how long the gate is now closed."""
        with self._lock:
            now = self._clock()
            if not self._storm:
                self._storm = True
                self.n_storms += 1
            self.n_hits += 1
            self._streak += 1
            wanted = retry_after_s if retry_after_s and retry_after_s > 0 else (
                self._base * (2 ** min(self._streak - 1, 8))
            )
            delay = min(self._max_wait, wanted)
            self._closed_until = max(self._closed_until, now + delay)
            self._probe_until = 0.0  # a fresh probe may go once the window elapses
            return self._closed_until - now

    def ok(self) -> None:
        """Record a success: the model is answering again, so open the gate."""
        if not self._storm:
            return
        with self._lock:
            self._storm = False
            self._streak = 0
            self._closed_until = 0.0
            self._probe_until = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            return {
                "name": self.name,
                "storming": self._storm,
                "closed_for_s": round(max(0.0, self._closed_until - now), 1),
                "storms": self.n_storms,
                "hits": self.n_hits,
                "probes": self.n_probes,
                "parked_s": round(self.parked_s, 1),
            }

    # ----------------------------------------------------------------- private
    def _n_probe_hit(self) -> None:
        self.n_probes += 1


_gates: dict[str, StormGate] = {}
_gates_lock = threading.Lock()


def storm_gate(name: str) -> StormGate:
    """The process-wide gate for ``name`` (``"gemini:gemini-3.7-flash"``)."""
    with _gates_lock:
        gate = _gates.get(name)
        if gate is None:
            gate = StormGate(name)
            _gates[name] = gate
        return gate


def all_gates() -> list[StormGate]:
    """Every gate this process has created (for ``3dcv doctor --live``)."""
    with _gates_lock:
        return list(_gates.values())
