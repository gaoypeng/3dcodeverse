"""Cache-friendly prompt assembly + a measurement for whether it worked.

Gemini's implicit cache (and Anthropic's explicit one) only ever matches a
**prefix**: the shared part must be byte-identical *from token 0*.  One
variable line near the top — a timestamp, a round number, a shuffled view list —
moves everything after it out of the cache.  Cache reads cost 10% of input, so
the whole difference between a $0.75/M prompt and a $0.075/M prompt is where the
volatile blocks sit.

Usage::

    from codeverse.cost import order_blocks, Block
    blocks = [Block("system rules", SYSTEM, stable=True),
              Block("rubric", rubric_md, stable=True),
              Block("round", f"round {i}", stable=False)]
    prompt = render_blocks(order_blocks(blocks))

:func:`prefix_report` measures a real sequence of prompts: how many tokens they
share, and how many tokens each call pays full price for because of divergence.

**Measured, and NOT applied to the generation prompts (2026-08-23).**  Wave 2
reordered ``generate_static`` / ``generate_articulated`` / ``refine_object``
stable-prefix-first, by hoisting a shared head into every one of them.  Measured
on the real templates (``prefix_report`` over one baseline + three refines):

===============================  ==============================  ===============
                                 per-prompt tokens               mean/call
===============================  ==============================  ===============
task-first (shipped, restored)   5,072 / 1,325 / 1,325 / 1,325    2,261
stable-head-first (reverted)     5,110 / 5,211 / 5,211 / 5,211    5,186
===============================  ==============================  ===============

The head was **duplicated** into every prompt rather than shared with anything
already being sent, so the run-level change is +2,925 tokens on the average call
(+3,886 on every refine) — and the prompt is re-sent on every turn of the
session, so it is that figure times the turn count.  What it bought was a 4,970
token shared prefix, below the ~12k floor §4 of ``docs/COST.md`` measured for
Gemini's implicit cache, so there is no cache read to set against it.  The one
prefix that is genuinely shared without duplication — the api-agent's system
prompt (``AGENTS.md``, 4,879 tokens) plus its tool declarations (1,691) — already
sits first in every request and totals **6,570 tokens**, also below the floor.

So: keep these helpers for measuring and for any future prompt family that is
big enough, but do not reorder a prompt for the cache without measuring the
prefix against the floor first.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from codeverse.cost.guard import text_tokens


@dataclass(frozen=True)
class Block:
    """One labelled chunk of a prompt.  ``stable`` blocks are identical across
    every call in a family (system prompt, contract, cookbook, rubric, defect
    checklist); everything else is volatile."""

    name: str
    text: str
    stable: bool = False
    order: int = 0  # tie-break inside the stable / volatile group

    @property
    def tokens(self) -> int:
        return text_tokens(self.text)


def order_blocks(blocks: Sequence[Block]) -> list[Block]:
    """Stable blocks first (input order preserved), volatile blocks after.

    This is the whole optimisation: the longest possible identical prefix, then
    everything that changes per call."""
    stable = [b for b in blocks if b.stable]
    volatile = [b for b in blocks if not b.stable]
    stable.sort(key=lambda b: b.order)
    volatile.sort(key=lambda b: b.order)
    return stable + volatile


def render_blocks(blocks: Sequence[Block], *, sep: str = "\n\n") -> str:
    return sep.join(b.text for b in blocks if b.text)


def prefix_signature(blocks: Sequence[Block]) -> str:
    """Hash of the stable prefix — log it per call; if it ever changes between
    two calls that should share a cache, the cache was silently lost."""
    h = hashlib.sha256()
    for b in blocks:
        if not b.stable:
            break
        h.update(b.text.encode("utf-8", "replace"))
    return h.hexdigest()[:12]


def common_prefix_chars(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


@dataclass
class PrefixReport:
    """What a family of prompts shares."""

    n_prompts: int = 0
    prefix_chars: int = 0
    prefix_tokens: int = 0
    mean_tokens: int = 0
    divergent_tokens: int = 0  # mean tokens after the shared prefix
    per_prompt: list[int] = field(default_factory=list)

    @property
    def shared_fraction(self) -> float:
        return self.prefix_tokens / self.mean_tokens if self.mean_tokens else 0.0

    def savings_usd(self, price_input: float, price_cached: float, *, calls: int | None = None) -> float:
        """USD saved per ``calls`` calls by the shared prefix being a cache hit
        instead of fresh input (the first call still pays full price)."""
        n = (calls if calls is not None else self.n_prompts) - 1
        return max(0, n) * self.prefix_tokens * max(0.0, price_input - price_cached) / 1e6


def prefix_report(prompts: Sequence[str]) -> PrefixReport:
    """Measure the common prefix of a real prompt sequence."""
    if not prompts:
        return PrefixReport()
    shared = prompts[0]
    for p in prompts[1:]:
        shared = shared[: common_prefix_chars(shared, p)]
    sizes = [text_tokens(p) for p in prompts]
    prefix_tokens = text_tokens(shared)
    return PrefixReport(
        n_prompts=len(prompts), prefix_chars=len(shared), prefix_tokens=prefix_tokens,
        mean_tokens=int(sum(sizes) / len(sizes)), per_prompt=sizes,
        divergent_tokens=int(sum(sizes) / len(sizes)) - prefix_tokens,
    )


def cache_efficiency(usages: Sequence[object]) -> tuple[int, int, float]:
    """``(cached, input, hit rate)`` over a sequence of ``Usage``-like objects —
    the after-the-fact check that the ordering actually paid off."""
    cached = sum(int(getattr(u, "cached_tokens", 0)) for u in usages)
    total = sum(int(getattr(u, "input_tokens", 0)) for u in usages)
    return cached, total, (cached / total if total else 0.0)


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


def session_cache(rows: Sequence[object], *, key: str = "label") -> list[SessionCache]:
    """Per-session cache behaviour from ledger rows (``codeverse.cost.CallCost``).

    Sessions are ``(round, session_key(<key>))`` groups in ledger order, so the first row of a
    group is that session's cold turn — the one the audit shows pays full price
    (docs/COST.md §4).  Use it to check whether a prompt-ordering change actually
    moved ``cached_tokens``, not just the prompt."""
    out: dict[tuple[object, str], SessionCache] = {}
    order: list[tuple[object, str]] = []
    for row in rows:
        k = (getattr(row, "round", None), session_key(str(getattr(row, key, "") or "")))
        s = out.get(k)
        if s is None:
            s = out[k] = SessionCache(key=f"r{k[0] if k[0] is not None else '-'}:{k[1]}",
                                      first_input=int(getattr(row, "input_tokens", 0)),
                                      first_cached=int(getattr(row, "cached_tokens", 0)),
                                      price_input=float(getattr(row, "price_input", 0.0)),
                                      price_cached=float(getattr(row, "price_cached", 0.0)))
            order.append(k)
        s.n_calls += 1
        s.input_tokens += int(getattr(row, "input_tokens", 0))
        s.cached_tokens += min(int(getattr(row, "cached_tokens", 0)), int(getattr(row, "input_tokens", 0)))
        s.cost_usd += float(getattr(row, "cost_usd", 0.0))
    return [out[k] for k in order]
