"""Per-session cache behaviour, read back out of the ledger.

Gemini's implicit cache (and Anthropic's explicit one) only ever matches a
**prefix**: the shared part must be byte-identical from token 0, and cache reads
cost 10 % of input.  This module answers the after-the-fact question — did a
session's later turns actually read the cache, and what did that save?

It used to also carry a prompt-ASSEMBLY half (``Block``/``order_blocks``/
``prefix_report``/``cache_efficiency``) for building cache-friendly prompts.  That
experiment was measured and reverted (docs/COST.md §13: every prompt family here
sits below the provider's minimum cacheable prefix, so reordering bought nothing),
and the code sat with no production caller until it was deleted on 2026-08-28.

``3dcv cost cache <slug>`` is the reader.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


# --------------------------------------------------------------------------- ledger view
@dataclass
class SessionCache:
    """Cache behaviour of ONE agent session (or one-shot call) as the ledger saw it."""

    key: str
    n_calls: int = 0
    first_input: int = 0
    first_cached: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    price_input: float = 0.0
    price_cached: float = 0.0

    @property
    def cached_fraction(self) -> float:
        return self.cached_tokens / self.input_tokens if self.input_tokens else 0.0

    @property
    def first_cached_fraction(self) -> float:
        return self.first_cached / self.first_input if self.first_input else 0.0

    @property
    def saved_usd(self) -> float:
        """USD the cache reads saved against paying full input price for them."""
        return self.cached_tokens * max(0.0, self.price_input - self.price_cached) / 1e6

    @property
    def cold_usd(self) -> float:
        """USD still paid at full price for the uncached head of the session."""
        return self.first_input * self.price_input / 1e6 if not self.first_cached else 0.0


def session_key(label: str) -> str:
    """Collapse a per-call label to its session: ``api-agent:refine_legs:t7`` →
    ``refine_legs``, ``judge:static_object_v1:r02:s1`` → ``judge:static_object_v1``.

    A session is what shares a prompt prefix: every turn of one agent session,
    every sample of one verdict."""
    parts = [p for p in (label or "").split(":") if p]
    if parts and parts[0] == "api-agent":   # historical ledgers only (backend deleted 2026-08-28)
        parts = parts[1:]
    while parts and len(parts[-1]) > 1 and parts[-1][0] in "tsr" and parts[-1][1:].isdigit():
        parts = parts[:-1]
    return ":".join(parts)


def session_cache(rows: Sequence[object]) -> list[SessionCache]:
    """Per-session cache behaviour from ledger rows (``codeverse.cost.CallCost``).

    Sessions are ``(round, session_key(<key>))`` groups in ledger order, so the first row of a
    group is that session's cold turn — the one the audit shows pays full price
    (docs/COST.md §4).  Use it to check whether a prompt-ordering change actually
    moved ``cached_tokens``, not just the prompt."""
    out: dict[tuple[object, str], SessionCache] = {}   # insertion order IS ledger order
    for row in rows:
        k = (getattr(row, "round", None), session_key(str(getattr(row, "label", "") or "")))
        s = out.get(k)
        if s is None:
            s = out[k] = SessionCache(key=f"r{k[0] if k[0] is not None else '-'}:{k[1]}",
                                      first_input=int(getattr(row, "input_tokens", 0)),
                                      first_cached=int(getattr(row, "cached_tokens", 0)),
                                      price_input=float(getattr(row, "price_input", 0.0)),
                                      price_cached=float(getattr(row, "price_cached", 0.0)))
        s.n_calls += 1
        s.input_tokens += int(getattr(row, "input_tokens", 0))
        s.cached_tokens += min(int(getattr(row, "cached_tokens", 0)), int(getattr(row, "input_tokens", 0)))
        s.cost_usd += float(getattr(row, "cost_usd", 0.0))
    return list(out.values())
