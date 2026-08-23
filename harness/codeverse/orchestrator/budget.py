"""Budget guard: hard per-run ceilings on cost and wall time.

Every model / agent call yields a ``Usage``; the track charges it here.  When
either ceiling is crossed ``BudgetExceeded`` is raised so the round loop can
stop cleanly (status ``budget``) instead of silently overspending.
"""

from __future__ import annotations

import threading
import time

from codeverse.contracts.common import Budget, Usage


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

    def __init__(self, budget: Budget, start_time: float | None = None):
        self.budget = budget
        self.start_time = time.time() if start_time is None else start_time
        self.spent = Usage()
        self._lock = threading.Lock()
        self.calls = 0

    # ----------------------------------------------------------------- accounting
    def charge(self, usage: Usage | None) -> None:
        """Add ``usage`` to the running total, then enforce the ceilings."""
        if usage is not None:
            with self._lock:
                self.spent = self.spent + usage
                self.calls += 1
        self.check()

    def elapsed_minutes(self) -> float:
        return (time.time() - self.start_time) / 60.0

    def check(self) -> None:
        """Raise ``BudgetExceeded`` if any ceiling has been crossed."""
        spent = self.spent.cost_usd
        elapsed = self.elapsed_minutes()
        if spent > self.budget.max_usd:
            raise BudgetExceeded(
                f"cost ${spent:.3f} exceeds max_usd ${self.budget.max_usd:.2f}",
                spent_usd=spent, elapsed_min=elapsed,
            )
        if elapsed > self.budget.max_minutes:
            raise BudgetExceeded(
                f"elapsed {elapsed:.1f} min exceeds max_minutes {self.budget.max_minutes:.1f}",
                spent_usd=spent, elapsed_min=elapsed,
            )

    def ok(self) -> bool:
        """True when no ceiling is crossed (non-raising variant of ``check``)."""
        try:
            self.check()
        except BudgetExceeded:
            return False
        return True

    def remaining(self) -> dict[str, float]:
        """Remaining headroom: ``{"usd": ..., "minutes": ..., "fraction": ...}``."""
        usd = max(0.0, self.budget.max_usd - self.spent.cost_usd)
        minutes = max(0.0, self.budget.max_minutes - self.elapsed_minutes())
        frac_usd = usd / self.budget.max_usd if self.budget.max_usd > 0 else 0.0
        frac_min = minutes / self.budget.max_minutes if self.budget.max_minutes > 0 else 0.0
        return {"usd": usd, "minutes": minutes, "fraction": min(frac_usd, frac_min)}

    def summary(self) -> dict[str, float | int]:
        return {
            "spent_usd": round(self.spent.cost_usd, 4),
            "elapsed_min": round(self.elapsed_minutes(), 2),
            "calls": self.calls,
            "input_tokens": self.spent.input_tokens,
            "output_tokens": self.spent.output_tokens,
        }
