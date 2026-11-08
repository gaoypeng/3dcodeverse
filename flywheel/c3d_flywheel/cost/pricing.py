"""USD price table (per 1M tokens) + ``estimate_cost`` + price provenance.

Each row carries its provenance (source status and check date).  Long-prompt tiers
are modelled (``Price.long_context``); cache storage, 1h cache writes, batch and fast-mode
multipliers are not.  Unknown models price at 0.0 and warn once.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from codeverse3d.contracts.common import Usage

log = logging.getLogger(__name__)

#: date the tables below were last reconciled against the providers' pricing pages
CHECKED = "2026-08-23"


@dataclass(frozen=True)
class LongContext:
    """Prices that replace the standard ones once the prompt exceeds ``threshold``."""

    threshold: int
    input: float
    output: float
    cached: float = 0.0


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens.  ``cached`` = cache-read input, ``cache_write`` = cache creation;
    thoughts bill at the output rate.  ``status``: verified | inferred (nearest sibling) |
    unverified (not on the pricing page)."""

    input: float
    output: float
    cached: float = 0.0
    cache_write: float | None = None
    approximate: bool = False
    long_context: LongContext | None = None
    checked: str = CHECKED
    status: str = "verified"
    note: str = ""


# fmt: off
PRICES: dict[tuple[str, str], Price] = {
    # ---------------------------------------------------------------- gemini
    ("gemini", "gemini-4-argon"):             Price(2.00, 10.00, 0.10, checked="2026-10-13",
                                                    note="intro rate through 2026-11-30, then 4.00/20.00/0.20"),
    ("gemini", "gemini-3.8-flash"):           Price(0.75, 3.75, 0.075, checked="2026-09-21",
                                                    note="intro rate through 2026-12-31, then 1.50/7.50/0.15"),
    ("gemini", "gemini-3.1-pro-preview"):     Price(2.00, 12.00, 0.20,
                                                    long_context=LongContext(200_000, 4.00, 18.00, 0.40),
                                                    note=">200k prompts: 4.00/18.00/0.40 (modelled)"),
    ("gemini", "gemini-3-pro-preview"):       Price(2.00, 12.00, 0.20,
                                                    long_context=LongContext(200_000, 4.00, 18.00, 0.40),
                                                    note=">200k prompts: 4.00/18.00 (modelled)"),
    ("gemini", "gemini-3-flash-preview"):     Price(0.50, 3.00, 0.05),
    ("gemini", "gemini-2.5-pro"):             Price(1.25, 10.00, 0.125,
                                                    long_context=LongContext(200_000, 2.50, 15.00, 0.25),
                                                    note=">200k prompts: 2.50/15.00/0.25 (modelled)"),
    ("gemini", "gemini-2.5-flash"):           Price(0.30, 2.50, 0.03, note="cached was 0.075 here until 2026-08-23; published 0.03"),
    ("gemini", "gemini-2.5-flash-lite"):      Price(0.10, 0.40, 0.01, note="cached was 0.025 here until 2026-08-23; published 0.01"),
    # ------------------------------------------------------------- anthropic
    ("anthropic", "claude-fable-5-1"):        Price(10.00, 50.00, 0.25, cache_write=12.50, checked="2026-10-13",
                                                    note="1h cache write 20.00 (not modelled)"),
    ("anthropic", "claude-opus-5-5"):         Price(4.00, 20.00, 0.20, cache_write=5.00, checked="2026-10-13",
                                                    note="fast mode is 8.00/40.00 (not modelled)"),
    ("anthropic", "claude-sonnet-5-5"):       Price(2.00, 10.00, 0.10, cache_write=2.50, checked="2026-10-13"),
    # haiku-5.5: prompts over 100k tokens bill the whole request at the long tier
    ("anthropic", "claude-haiku-5-5"):        Price(0.10, 0.50, 0.01, cache_write=0.125, checked="2026-10-13",
                                                    long_context=LongContext(100_000, 0.50, 2.50, 0.05)),
    # the small model claude-code reports next to the main one
    ("anthropic", "claude-haiku-4-5"):        Price(1.00, 5.00, 0.10, cache_write=1.25, checked="2026-10-13"),
    # ---------------------------------------------------------------- openai
    # >272k input bills the whole request at 2x input/cache and 1.5x output
    ("openai", "gpt-6-astra"):               Price(10.00, 50.00, 1.00, cache_write=12.50, checked="2026-10-13",
                                                    long_context=LongContext(272_000, 20.00, 75.00, 2.00)),
    ("openai", "gpt-6.1-sol"):               Price(2.00, 10.00, 0.10, cache_write=2.50, checked="2026-10-13",
                                                    long_context=LongContext(272_000, 4.00, 15.00, 0.20)),
    ("openai", "gpt-6-sol"):                 Price(2.00, 10.00, 0.20, cache_write=2.50, checked="2026-10-13",
                                                    long_context=LongContext(272_000, 4.00, 15.00, 0.40)),
    ("openai", "gpt-6-luna"):                Price(0.10, 0.50, 0.01, cache_write=0.125, checked="2026-10-13",
                                                    long_context=LongContext(272_000, 0.20, 0.75, 0.02)),
}
# fmt: on

_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


def _normalise(model: str) -> str:
    return model.strip().lower()


#: reasoning efforts agy spells into its model ids (``gemini-3.8-flash-medium``): same model, same rate
EFFORTS = ("low", "medium", "high")
# words that may follow a priced model id without changing which model it is
_VERSION_WORDS = frozenset({"latest", "preview", "exp", "beta", "alpha", "stable", "snapshot", *EFFORTS})


def _is_version_suffix(rest: str) -> bool:
    """True when ``rest`` (what follows a table key) is only a version/date/channel tag,
    not a sibling model name (``-mini``, ``.5``)."""
    if not rest or rest[0] not in "-_@:":
        return False
    segments = rest[1:].split("-")
    return all(seg.isdigit() or seg in _VERSION_WORDS for seg in segments)


def _match(provider: str, model: str) -> tuple[tuple[str, str], Price] | None:
    m = _normalise(model)
    exact = PRICES.get((provider, m))
    if exact is not None:
        return (provider, m), exact
    best: tuple[int, tuple[str, str], Price] | None = None
    for (prov, key), price in PRICES.items():
        if prov != provider or not m.startswith(key) or not _is_version_suffix(m[len(key) :]):
            continue
        if best is None or len(key) > best[0]:
            best = (len(key), (prov, key), price)
    return (best[1], best[2]) if best else None


def lookup_price(provider: str, model: str) -> Price | None:
    """Exact match, else the longest table key extended only by a version/date suffix.
    A sibling name (``gpt-6.1-sol-mini``) is unknown → ``None``, never a sibling's rate."""
    found = _match(provider, model)
    return found[1] if found else None


@dataclass(frozen=True)
class PriceRow:
    """A resolved price plus its audit trail — what ``c3d-flywheel cost prices`` prints."""

    provider: str
    model: str
    key: str  # the PRICES key that matched ("" when unknown)
    match: str  # exact | prefix | unknown
    price: Price | None

    @property
    def approximate(self) -> bool:
        """True when the dollar is not exact: no row, a non-verified row, or flagged."""
        return self.price is None or self.price.approximate or self.price.status != "verified"

    @property
    def checked(self) -> str:
        return self.price.checked if self.price else ""

    @property
    def status(self) -> str:
        return self.price.status if self.price else "unknown"


def price_provenance(provider: str, model: str) -> PriceRow:
    """Resolve ``provider:model`` to a price and its source; unknown → ``match="unknown"``."""
    prov = (provider or "").strip().lower()
    found = _match(prov, model)
    if not found:
        return PriceRow(prov, _normalise(model), "", "unknown", None)
    key, price = found
    match = "exact" if key[1] == _normalise(model) else "prefix"
    return PriceRow(prov, _normalise(model), key[1], match, price)


def unit_prices(provider: str, model: str, *, prompt_tokens: int = 0) -> tuple[float, float, float]:
    """``(input, cached, output)`` USD per 1M for a prompt of ``prompt_tokens`` (long tier
    past its threshold).  Unknown model → zeros."""
    price = lookup_price(provider, model)
    if price is None:
        return (0.0, 0.0, 0.0)
    lc = price.long_context
    if lc is not None and prompt_tokens > lc.threshold:
        return (lc.input, lc.cached, lc.output)
    return (price.input, price.cached, price.output)


def estimate_cost(provider: str, model: str, usage: Usage) -> float:
    """USD for one call.  ``input_tokens`` includes cached tokens (re-priced at the cache-read
    rate); ``thoughts_tokens`` bill as output, so callers must not also fold them into ``output_tokens``."""
    price = lookup_price(provider, model)
    if price is None:
        key = (provider, _normalise(model))
        with _warned_lock:
            if key not in _warned:
                _warned.add(key)
                log.warning("pricing: unknown model %s:%s — cost reported as 0.0", provider, model)
        return 0.0
    m = 1_000_000.0
    p_in, p_cached, p_out = unit_prices(provider, model, prompt_tokens=usage.input_tokens)
    cached = max(0, min(usage.cached_tokens, usage.input_tokens))
    uncached = max(0, usage.input_tokens - cached)
    cost = uncached * p_in / m
    cost += cached * p_cached / m
    cost += (usage.output_tokens + usage.thoughts_tokens) * p_out / m
    return cost


def openai_usage(backend: str, model: str, *, prompt: int, cached: int, completion: int, reasoning: int,
                 **extra: Any) -> Usage:
    """OpenAI-family tokens as a priced ``Usage``.  ``completion`` contains ``reasoning``;
    the reasoning share moves to ``thoughts_tokens`` so it is billed once."""
    usage = Usage(backend=backend, model=model, input_tokens=prompt, output_tokens=max(0, completion - reasoning),
                  cached_tokens=cached, thoughts_tokens=reasoning, **extra)
    usage.cost_usd = estimate_cost("openai", model, usage)
    return usage


def cache_write_surcharge(provider: str, model: str, cache_write_tokens: int) -> float:
    """Extra USD on top of the input rate ``estimate_cost`` already charged, for cache creation."""
    price = lookup_price(provider, model)
    if price is None or cache_write_tokens <= 0 or price.cache_write is None:
        return 0.0
    return cache_write_tokens * max(0.0, price.cache_write - price.input) / 1_000_000.0
