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
