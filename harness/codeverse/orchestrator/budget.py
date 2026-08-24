"""Budget guard: hard per-run ceilings on cost and wall time, plus a soft cap.

Every model / agent call yields a ``Usage``; the track charges it here.  When
either **hard** ceiling is crossed ``BudgetExceeded`` is raised so the round
loop can stop cleanly (status ``budget``) instead of silently overspending.

**One door for every dollar.**  ``charge`` and ``add`` are thin wrappers over
:meth:`BudgetGuard.spend`, which is the only place money enters the run: it
accumulates the ``Usage``, buckets it by stage and by round, appends one priced
row to the run's cost ledger (``<run>/cost_ledger.jsonl``,
``codeverse.cost.record_call``) and then — for ``charge`` — enforces the
ceilings.  The audit (docs/COST.md §6) found $4.80 of real spend that never
reached ``record.total_usage``: rounds the budget cut after the work was done,
retried ``.a2`` agent sessions and post-hoc texture passes.  Anything that goes
through ``spend`` is visible to the guard, to the record and to the ledger, so
that class of blindness cannot come back.

On top of that a **soft** sub-budget (``soft_fraction`` of the hard ceilings)
lets a track see the wall coming: ``soft_ok()`` / ``soft_exceeded()`` say
"the baseline has used its share — degrade now (fewer assets, single-shot
instead of an agent session) so the refine rounds still have money".  Crossing
the soft cap never raises; only the hard ceiling does.  ``grant_grace`` raises
the hard ceiling once so a budget-stopped run can still finalise (assemble +
build + render + judge) instead of dying with no score at all.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from codeverse.contracts.common import Budget, Usage

log = logging.getLogger(__name__)

#: stage label used when a caller does not say where the money went
OTHER_STAGE = "other"


class BudgetExceeded(RuntimeError):
    """Raised when a run crosses its USD or wall-clock ceiling."""

    def __init__(self, reason: str, *, spent_usd: float, elapsed_min: float):
        super().__init__(reason)
        self.reason = reason
        self.spent_usd = spent_usd
        self.elapsed_min = elapsed_min


class BudgetGuard:
    """Thread-safe accumulator of ``Usage`` against a ``Budget``.

    ``charge`` adds usage and then calls ``check``; ``check`` raises
    ``BudgetExceeded`` when ``max_usd`` or ``max_minutes`` is exceeded.
    """

    def __init__(self, budget: Budget, start_time: float | None = None, *, soft_fraction: float = 1.0,
                 run: str = "", ledger: str | Path | Any | None = None):
        self.budget = budget
        self.start_time = time.time() if start_time is None else start_time
        self.spent = Usage()
        self._lock = threading.Lock()
        self.calls = 0
        #: fraction of the hard ceilings the *baseline* may use (1.0 = no soft cap)
        self.soft_fraction = min(1.0, max(0.0, float(soft_fraction)))
        #: one-off extension of the HARD ceilings (finalise/salvage headroom)
        self.grace_usd = 0.0
        self.grace_minutes = 0.0
        #: run slug + ledger sink for the per-call rows (None = do not write a ledger)
        self.run = run
        self.ledger = ledger
        #: stage -> USD and round -> {stage -> USD}, so a round can report what it burned
        self.by_stage: dict[str, float] = {}
        self.by_round: dict[int, dict[str, float]] = {}

    # ----------------------------------------------------------------- accounting
    def spend(
        self,
        usage: Usage | None,
        *,
        stage: str = OTHER_STAGE,
        role: str | None = None,
        label: str = "",
        round_index: int | None = None,
        outcome: str = "ok",
        enforce: bool = True,
    ) -> None:
        """THE door every dollar goes through.

        Accumulates ``usage``, buckets it by ``stage`` (and by round), appends a
        priced row to the run's cost ledger, and — when ``enforce`` — raises
        ``BudgetExceeded`` if a hard ceiling is now crossed.  Accounting never
        fails a run: a broken ledger is logged, not propagated."""
        if usage is None:
            return
        with self._lock:
            self.spent = self.spent + usage
            self.calls += 1
            key = stage or OTHER_STAGE
            self.by_stage[key] = self.by_stage.get(key, 0.0) + float(usage.cost_usd)
            if round_index is not None:
                per = self.by_round.setdefault(int(round_index), {})
                per[key] = per.get(key, 0.0) + float(usage.cost_usd)
        self._ledger_row(usage, stage=key, role=role, label=label, round_index=round_index, outcome=outcome)
        if enforce:
            self.check()

    def charge(self, usage: Usage | None, **kw: Any) -> None:
        """Account for ``usage``, then enforce the ceilings (see :meth:`spend`)."""
        kw.setdefault("enforce", True)
        self.spend(usage, **kw)

    def add(self, usage: Usage | None, **kw: Any) -> None:
        """Account for ``usage`` WITHOUT enforcing the ceilings.

        For work that is already done and persisted (a completed judge verdict,
        a pairwise tie-break, the texture pass): the money is spent either way,
        and raising here would throw away a finished, paid-for result.  The
        round loop stops at its next ``ok()`` check instead.  The dollar is
        still bucketed and still written to the ledger — "not enforced" never
        means "not seen"."""
        kw["enforce"] = False
        self.spend(usage, **kw)

    def _ledger_row(self, usage: Usage, *, stage: str, role: str | None, label: str,
                    round_index: int | None, outcome: str) -> None:
        if self.ledger is None:
            return
        try:
            from codeverse.cost import per_call_metering, record_call

            # When the run is metered call by call (codeverse.cost.instrument wraps every
            # ChatModel / CodingAgent), those rows already ARE this dollar with per-call
            # tokens, cache hits and latency — one aggregate row on top would double count.
            if per_call_metering():
                return
            record_call(usage, run=self.run, stage=stage, role=role, round=round_index, label=label,
                        backend=usage.backend, model=usage.model, outcome=outcome, ledger=self.ledger)
        except Exception as e:  # noqa: BLE001 — accounting must never fail a run
            log.debug("cost ledger row failed (%s): %s", self.ledger, e)

    def mark(self) -> Usage:
        """Snapshot of the running total — diff it with :func:`usage_delta` to see
        what a round burned even when the round itself raised half-way."""
        return self.spent.model_copy(deep=True)

    def round_costs(self, round_index: int) -> dict[str, float]:
        """``{stage: USD}`` charged against one round index (empty when none)."""
        return dict(self.by_round.get(int(round_index), {}))

    def elapsed_minutes(self) -> float:
        return (time.time() - self.start_time) / 60.0

    # ----------------------------------------------------------------- ceilings
    @property
    def hard_usd(self) -> float:
        return self.budget.max_usd + self.grace_usd

    @property
    def hard_minutes(self) -> float:
        return self.budget.max_minutes + self.grace_minutes

    def grant_grace(self, *, usd: float = 0.0, minutes: float = 0.0) -> None:
        """Extend the HARD ceilings (never shrink them).  Used once by finalise /
        salvage so a run that tripped the budget mid-stage can still deliver a
        judged round instead of no score at all."""
        with self._lock:
            self.grace_usd = max(self.grace_usd, max(0.0, float(usd)))
            self.grace_minutes = max(self.grace_minutes, max(0.0, float(minutes)))

    def check(self) -> None:
        """Raise ``BudgetExceeded`` if any hard ceiling has been crossed."""
        spent = self.spent.cost_usd
        elapsed = self.elapsed_minutes()
        if spent > self.hard_usd:
            raise BudgetExceeded(
                f"cost ${spent:.3f} exceeds max_usd ${self.hard_usd:.2f}",
                spent_usd=spent, elapsed_min=elapsed,
            )
        if elapsed > self.hard_minutes:
            raise BudgetExceeded(
                f"elapsed {elapsed:.1f} min exceeds max_minutes {self.hard_minutes:.1f}",
                spent_usd=spent, elapsed_min=elapsed,
            )

    # ----------------------------------------------------------------- soft cap
    def soft_limits(self) -> tuple[float, float]:
        """(usd, minutes) the soft sub-budget allows (grace is hard-only)."""
        return self.budget.max_usd * self.soft_fraction, self.budget.max_minutes * self.soft_fraction

    def soft_exceeded(self) -> str:
        """Reason string when the soft sub-budget is used up, else ``""``."""
        usd, minutes = self.soft_limits()
        spent, elapsed = self.spent.cost_usd, self.elapsed_minutes()
        if spent > usd:
            return f"cost ${spent:.3f} exceeds soft cap ${usd:.2f} ({self.soft_fraction:.0%} of ${self.budget.max_usd:.2f})"
        if elapsed > minutes:
            return f"elapsed {elapsed:.1f} min exceeds soft cap {minutes:.1f} min ({self.soft_fraction:.0%} of {self.budget.max_minutes:.1f})"
        return ""

    def soft_ok(self) -> bool:
        """True while the baseline still has its share of the budget."""
        return not self.soft_exceeded()

    def soft_remaining(self) -> dict[str, float]:
        """Headroom left inside the soft sub-budget (never negative)."""
        usd, minutes = self.soft_limits()
        return {"usd": max(0.0, usd - self.spent.cost_usd), "minutes": max(0.0, minutes - self.elapsed_minutes())}

    def ok(self) -> bool:
        """True when no ceiling is crossed (non-raising variant of ``check``)."""
        try:
            self.check()
        except BudgetExceeded:
            return False
        return True

    def remaining(self) -> dict[str, float]:
        """Remaining headroom: ``{"usd": ..., "minutes": ..., "fraction": ...}``."""
        usd = max(0.0, self.hard_usd - self.spent.cost_usd)
        minutes = max(0.0, self.hard_minutes - self.elapsed_minutes())
        frac_usd = usd / self.hard_usd if self.hard_usd > 0 else 0.0
        frac_min = minutes / self.hard_minutes if self.hard_minutes > 0 else 0.0
        return {"usd": usd, "minutes": minutes, "fraction": min(frac_usd, frac_min)}

    def timeout_s(self, want_s: float, *, floor_s: float = 60.0, soft: bool = True) -> int:
        """``want_s`` clipped to the wall-clock actually left (soft cap when ``soft``).

        A generation session must never be allowed to outlive the run's budget:
        the greenhouse scene lost 54 minutes to zone agents that kept working
        after the ceiling had already been crossed."""
        left = (self.soft_remaining()["minutes"] if soft else self.remaining()["minutes"]) * 60.0
        return int(max(floor_s, min(float(want_s), left) if left > 0 else floor_s))

    def summary(self) -> dict[str, float | int]:
        return {
            "spent_usd": round(self.spent.cost_usd, 4),
            "elapsed_min": round(self.elapsed_minutes(), 2),
            "soft_fraction": round(self.soft_fraction, 3),
            "grace_usd": round(self.grace_usd, 4),
            "calls": self.calls,
            "input_tokens": self.spent.input_tokens,
            "output_tokens": self.spent.output_tokens,
        }

    def stage_summary(self) -> dict[str, float]:
        """``{stage: USD}`` over the whole run (what the ``cost.round`` events add up to)."""
        return {k: round(v, 6) for k, v in sorted(self.by_stage.items(), key=lambda kv: -kv[1])}


def usage_delta(after: Usage, before: Usage) -> Usage:
    """``after - before`` field by field (never negative).  Used to report what a
    round burned when it raised before it could fold its own usage together."""
    return Usage(
        backend=after.backend or before.backend,
        model=after.model or before.model,
        input_tokens=max(0, after.input_tokens - before.input_tokens),
        output_tokens=max(0, after.output_tokens - before.output_tokens),
        cached_tokens=max(0, after.cached_tokens - before.cached_tokens),
        thoughts_tokens=max(0, after.thoughts_tokens - before.thoughts_tokens),
        tool_calls=max(0, after.tool_calls - before.tool_calls),
        cost_usd=max(0.0, after.cost_usd - before.cost_usd),
        latency_ms=max(0, after.latency_ms - before.latency_ms),
    )
