"""Bounded parallel fan-out with per-item timing and error capture.

``fan_out`` never raises for a failing item: the result list holds either the
item's return value or the exception, in input order, so callers decide what a
partial failure means (a failed asset is skipped; a failed part task is
retried).  Exceptions that are ``BaseException`` but not ``Exception``
(KeyboardInterrupt, SystemExit) propagate.
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class FanOutReport:
    """Timing/outcome per item, for events and logs."""

    label: str
    n_items: int
    n_ok: int
    n_failed: int
    durations_s: list[float] = field(default_factory=list)
    total_s: float = 0.0


def _in_caller_context(snapshot: contextvars.Context, fn: Callable[[int], None], i: int) -> None:
    """Run ``fn(i)`` with the values the caller's context held at fan-out time.

    ``Context.run`` cannot be re-entered from several threads, so each worker
    replays the snapshot's variables into its own (already fresh) context.
    """
    for var, value in snapshot.items():
        var.set(value)
    fn(i)


def fan_out[T, R](
    items: Sequence[T] | Iterable[T],
    fn: Callable[[T], R],
    max_workers: int = 8,
    *,
    label: str = "fanout",
    item_name: Callable[[T], str] | None = None,
    on_done: Callable[[int, T, R | Exception, float], None] | None = None,
) -> list[R | Exception]:
    """Run ``fn`` over ``items`` in a thread pool; return results in input order.

    Each result is the return value or the raised ``Exception``.  ``on_done``
    (if given) is called from the worker thread with ``(index, item, result,
    seconds)`` — keep it cheap and thread-safe (e.g. an EventLog.emit).

    ``max_workers`` is a *fallback*: every caller in the harness states its own,
    sized from the measured ceilings in ``docs/COST.md`` Part III
    (``Settings.limits`` for the subprocess side, ``Settings.rate.max_in_flight``
    for model calls).  8 is the largest width that is inside both knees.
    """
    items = list(items)
    # Workers inherit the caller's context so ambient state set with contextvars
    # (the cost ledger's run/stage/role attribution) follows a parallel judge
    # sample, best-of-N candidate or bench cell instead of falling back to the
    # process default.
    ctx_snapshot = contextvars.copy_context()
    results: list[R | Exception] = [None] * len(items)  # type: ignore[list-item]
    if not items:
        return results
    workers = max(1, min(max_workers, len(items)))
    t_all = time.time()
    durations = [0.0] * len(items)

    def _run(i: int) -> None:
        item = items[i]
        name = item_name(item) if item_name else str(i)
        t0 = time.time()
        try:
            out: R | Exception = fn(item)
        except Exception as e:  # noqa: BLE001 — captured per item by design
            out = e
            log.warning("%s[%s] failed: %s: %s", label, name, type(e).__name__, e)
        dt = time.time() - t0
        durations[i] = dt
        results[i] = out
        log.info("%s[%s] done in %.1fs (%s)", label, name, dt, "error" if isinstance(out, Exception) else "ok")
        if on_done is not None:
            on_done(i, item, out, dt)

    if workers == 1:
        for i in range(len(items)):
            _run(i)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=label) as pool:
            list(pool.map(lambda i: contextvars.copy_context().run(_in_caller_context, ctx_snapshot, _run, i),
                          range(len(items))))

    n_failed = sum(1 for r in results if isinstance(r, Exception))
    report = FanOutReport(label, len(items), len(items) - n_failed, n_failed, durations, time.time() - t_all)
    log.info("%s: %d/%d ok in %.1fs", label, report.n_ok, report.n_items, report.total_s)
    return results


def split_results[R](results: Sequence[R | Exception]) -> tuple[list[R], list[Exception]]:
    """Separate successes from failures (order preserved within each list)."""
    ok: list[R] = []
    bad: list[Exception] = []
    for r in results:
        (bad if isinstance(r, Exception) else ok).append(r)  # type: ignore[arg-type]
    return ok, bad
