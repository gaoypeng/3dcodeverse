"""Per-session cache behaviour, read back out of the ledger."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


# --------------------------------------------------------------------------- ledger view
@dataclass
class SessionCache:

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
    def saved_usd(self) -> float:
        return self.cached_tokens * max(0.0, self.price_input - self.price_cached) / 1e6

    @property
    def cold_usd(self) -> float:
        return self.first_input * self.price_input / 1e6 if not self.first_cached else 0.0


def session_key(label: str) -> str:
    parts = [p for p in (label or "").split(":") if p]
    while parts and len(parts[-1]) > 1 and parts[-1][0] in "tsr" and parts[-1][1:].isdigit():
        parts = parts[:-1]
    return ":".join(parts)


def session_cache(rows: Sequence[object]) -> list[SessionCache]:
    out: dict[tuple[object, str], SessionCache] = {}
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
