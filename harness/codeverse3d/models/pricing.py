"""USD price table (per 1M tokens) + ``estimate_cost`` + price provenance.

Every ``Usage`` is priced here (providers never return USD).  Each row carries its
provenance — where the number came from and when it was last checked — so a cost
report can say *which* price row produced a dollar and whether it was verified or
merely inferred (``3dcode cost prices``).

Only the standard (short-context) tier is modelled, **except** the documented
long-prompt tiers of Gemini pro, Claude Haiku 5.5 and GPT-6, which ``estimate_cost`` applies
automatically (``Price.long_context``).  Cache *storage* fees (Gemini explicit
caching, $/1M-tokens/hour), Anthropic 1h-cache writes, batch/flex discounts,
data-residency and fast-mode multipliers are **not** modelled — see
``docs/COST.md``.  Unknown models price at 0.0 and log a warning once per
(provider, model).
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

#: ``Price.source`` ids; a row with no source was read off its provider's own page
SOURCES: dict[str, str] = {
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
    "inferred": "inferred from the closest sibling model (no published row)",
    "provider-cost": "reproduced from a provider-reported cost in our own run telemetry",
}


@dataclass(frozen=True)
class LongContext:
    """Prices that replace the standard ones once the prompt exceeds ``threshold``."""

    threshold: int
    input: float
    output: float
    cached: float = 0.0


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens, and where the number came from.  ``cached`` = cache-read input;
    ``cache_write`` = cache creation (Anthropic explicit caching, 5-minute TTL);
    ``thoughts`` are billed at the output rate.  ``image_usd`` = per generated 1024² image
    for image-output models (their image tokens are billed per image, not per token).

    Provenance: ``source`` is a :data:`SOURCES` key ("" = the provider's own pricing page),
    ``checked`` the day it was last read there, and ``status``:

    * ``verified``   — read off the provider's public pricing page on ``checked``
    * ``inferred``   — no published row; copied from the nearest sibling
    * ``unverified`` — the model is not listed on the page (retired / alias / CLI-only)
    """

    input: float
    output: float
    cached: float = 0.0
    cache_write: float | None = None
    approximate: bool = False
    image_usd: float = 0.0
    long_context: LongContext | None = None
    source: str = ""
    checked: str = CHECKED
    status: str = "verified"
    note: str = ""


# fmt: off
PRICES: dict[tuple[str, str], Price] = {
    # ---------------------------------------------------------------- gemini
    # 4 argon: introductory rates valid through 2026-11-30; they double on 2026-12-01
    # ($4.00 / $20.00 / $0.20).  Update then.  No >200k tier; output includes thinking.
    ("gemini", "gemini-4-argon"):             Price(2.00, 10.00, 0.10, checked="2026-10-13",
                                                    note="intro rate through 2026-11-30, then 4.00/20.00/0.20"),
    # 3.6 / 3.7 / 3.8 flash: introductory rates valid through 2026-12-31; they double
    # on 2027-01-01 ($1.50 / $7.50 / $0.15).  Update then.  No >200k surcharge.
    ("gemini", "gemini-3.8-flash"):           Price(0.75, 3.75, 0.075, checked="2026-09-21",
                                                    note="intro rate through 2026-12-31, then 1.50/7.50/0.15"),
    ("gemini", "gemini-3.7-flash"):           Price(0.75, 3.75, 0.075,
                                                    note="intro rate through 2026-12-31, then 1.50/7.50/0.15"),
    ("gemini", "gemini-3.6-flash"):           Price(0.75, 3.75, 0.075, note="intro rate through 2026-12-31"),
    ("gemini", "gemini-3.5-flash"):           Price(1.50, 9.00, 0.15),
    ("gemini", "gemini-3.1-pro-preview"):     Price(2.00, 12.00, 0.20,
                                                    long_context=LongContext(200_000, 4.00, 18.00, 0.40),
                                                    note=">200k prompts: 4.00/18.00/0.40 (modelled)"),
    ("gemini", "gemini-3.1-flash-lite"):      Price(0.25, 1.50, 0.025, note="audio input is 2x; text/image/video modelled"),
    # image-output model: text/thinking output $3/M, IMAGE output $60/M ≈ $0.067
    # per 1K image (1290 tokens); 0.5K $0.045, 2K $0.134, 4K $0.151.  No cache row.
    ("gemini", "gemini-3.1-flash-image"):     Price(0.50, 3.00, 0.05, image_usd=0.067,
                                                    note="image output $60/M ≈ $0.067 per 1K image; 0.5K $0.045, 4K $0.151"),
    ("gemini", "gemini-3-pro-preview"):       Price(2.00, 12.00, 0.20,
                                                    long_context=LongContext(200_000, 4.00, 18.00, 0.40),
                                                    note=">200k prompts: 4.00/18.00 (modelled)"),
    ("gemini", "gemini-3-flash-preview"):     Price(0.50, 3.00, 0.05),
    ("gemini", "gemini-2.5-pro"):             Price(1.25, 10.00, 0.125,
                                                    long_context=LongContext(200_000, 2.50, 15.00, 0.25),
                                                    note=">200k prompts: 2.50/15.00/0.25 (modelled)"),
    ("gemini", "gemini-2.5-flash"):           Price(0.30, 2.50, 0.03, note="cached was 0.075 here until 2026-08-23; published 0.03"),
    ("gemini", "gemini-2.5-flash-lite"):      Price(0.10, 0.40, 0.01, note="cached was 0.025 here until 2026-08-23; published 0.01"),
    # image tokens are billed per image: 1024² = 1290 tokens = $0.039 ⇒ $30.23/M
    ("gemini", "gemini-2.5-flash-image"):     Price(0.30, 30.00, 0.03, image_usd=0.039, status="inferred",
                                                    note="output/M derived from $0.039 per 1290-token 1024² image"),
    # ------------------------------------------------------------- anthropic
    # Only the current generation is kept (owner, 2026-10-13): a model no longer
    # here prices at $0 with a warning; a recorded run keeps the cost it was booked at.
    ("anthropic", "claude-fable-5-1"):        Price(10.00, 50.00, 0.25, cache_write=12.50, checked="2026-10-13",
                                                    note="1h cache write 20.00 (not modelled)"),
    ("anthropic", "claude-opus-5-5"):         Price(4.00, 20.00, 0.20, cache_write=5.00, checked="2026-10-13",
                                                    note="fast mode is 8.00/40.00 (not modelled)"),
    ("anthropic", "claude-sonnet-5-5"):       Price(2.00, 10.00, 0.10, cache_write=2.50, checked="2026-10-13"),
    # haiku-5.5 is priced by prompt length: >100k tokens (cache reads and writes included)
    # pays 0.50/2.50/0.05 for the whole request; its 0.625 cache write there is not modelled.
    ("anthropic", "claude-haiku-5-5"):        Price(0.10, 0.50, 0.01, cache_write=0.125, checked="2026-10-13",
                                                    long_context=LongContext(100_000, 0.50, 2.50, 0.05)),
    # kept for the small model claude-code reports next to the main one (``claude-haiku-4-5-20251001``)
    ("anthropic", "claude-haiku-4-5"):        Price(1.00, 5.00, 0.10, cache_write=1.25, checked="2026-10-13"),
    # ---------------------------------------------------------------- openai
    # >272k input bills the whole request at 2x input/cache and 1.5x output.  CLI aggregate
    # usage is a notional estimate, not per-request billing.
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

#: per-image USD by requested pixel size, for models whose image output is billed
#: per image rather than per token (``Price.image_usd`` is the 1024² entry).
IMAGE_USD_BY_SIZE: dict[tuple[str, str], dict[int, float]] = {
    ("gemini", "gemini-3.1-flash-image"): {512: 0.045, 1024: 0.067, 2048: 0.134, 4096: 0.151},
    ("gemini", "gemini-2.5-flash-image"): {512: 0.039, 1024: 0.039, 2048: 0.039, 4096: 0.039},
}

_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


def _normalise(model: str) -> str:
    return model.strip().lower()


#: the reasoning efforts agy spells into its model ids (``gemini-3.7-flash-medium``): the same
#: model at the same per-token rate — unmatched, every agy session was priced $0 (live, 2026-09-23)
EFFORTS = ("low", "medium", "high")
# words that may follow a priced model id without changing which model it is
_VERSION_WORDS = frozenset({"latest", "preview", "exp", "beta", "alpha", "stable", "snapshot", *EFFORTS})


def _is_version_suffix(rest: str) -> bool:
    """True when ``rest`` (what follows a table key in a model id) is only a
    version / date / channel tag — ``-20260301``, ``@001``, ``-preview-05-20``,
    ``-latest`` — and not a sibling model name (``-nano``, ``-mini``, ``-codex``,
    ``.5``), which must price separately or be reported unknown."""
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
    """Exact match first, then the longest table key that ``model`` extends by a
    pure version/date suffix (``claude-opus-5-20260301``, ``gemini-3.7-flash-preview-09``).
    A different family name after the key (``gpt-6.1-sol-mini`` vs ``gpt-6.1-sol``) is NOT a
    match: unknown → ``None`` so the caller prices 0.0 and warns, never a sibling's rate."""
    found = _match(provider, model)
    return found[1] if found else None


@dataclass(frozen=True)
class PriceRow:
    """A resolved price plus its audit trail — what ``3dcode cost prices`` prints."""

    provider: str
    model: str
    key: str  # the PRICES key that matched ("" when unknown)
    match: str  # exact | prefix | unknown
    price: Price | None

    @property
    def approximate(self) -> bool:
        """True when the dollar cannot be trusted to the cent: no row, an
        inferred/unverified row, or a row flagged ``approximate``."""
        return self.price is None or self.price.approximate or self.price.status != "verified"

    @property
    def checked(self) -> str:
        return self.price.checked if self.price else ""

    @property
    def status(self) -> str:
        return self.price.status if self.price else "unknown"


def price_provenance(provider: str, model: str) -> PriceRow:
    """Resolve ``provider:model`` to a price **and its source**.  Never raises;
    an unknown model comes back as ``match="unknown"``, ``price=None``."""
    prov = (provider or "").strip().lower()
    found = _match(prov, model)
    if not found:
        return PriceRow(prov, _normalise(model), "", "unknown", None)
    key, price = found
    match = "exact" if key[1] == _normalise(model) else "prefix"
    return PriceRow(prov, _normalise(model), key[1], match, price)


def unit_prices(provider: str, model: str, *, prompt_tokens: int = 0) -> tuple[float, float, float]:
    """``(input, cached, output)`` USD per 1M actually charged for a prompt of
    ``prompt_tokens`` — the long-context tier when the model has one and the
    prompt crosses its threshold.  Unknown model → ``(0, 0, 0)``."""
    price = lookup_price(provider, model)
    if price is None:
        return (0.0, 0.0, 0.0)
    lc = price.long_context
    if lc is not None and prompt_tokens > lc.threshold:
        return (lc.input, lc.cached, lc.output)
    return (price.input, price.cached, price.output)


def per_image_usd(provider: str, model: str, *, size: int = 1024) -> float:
    """Per-generated-image price for image-output models (0.0 when the model
    bills images as ordinary output tokens)."""
    found = _match((provider or "").strip().lower(), model)
    if not found:
        return 0.0
    key, price = found
    by_size = IMAGE_USD_BY_SIZE.get(key)
    if by_size:
        return by_size.get(size, price.image_usd)
    return price.image_usd


def estimate_cost(provider: str, model: str, usage: Usage) -> float:
    """USD for one call.  ``usage.input_tokens`` is the TOTAL prompt size (cached
    tokens included); cached tokens are re-priced at the cache-read rate.
    ``usage.thoughts_tokens`` are billed as output when the provider reports
    them separately from ``output_tokens`` (Gemini); Anthropic/OpenAI fold
    reasoning into ``output_tokens`` already, so callers must not double count."""
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
    """OpenAI-family tokens (the SDK and codex alike) as a priced ``Usage``.  ``completion``
    already CONTAINS ``reasoning`` (measured on codex: output − reasoning tracks the answer
    length), so the reasoning share moves to ``thoughts_tokens`` — still visible, and billed
    once by :func:`estimate_cost` (output + thoughts), never twice."""
    usage = Usage(backend=backend, model=model, input_tokens=prompt, output_tokens=max(0, completion - reasoning),
                  cached_tokens=cached, thoughts_tokens=reasoning, **extra)
    usage.cost_usd = estimate_cost("openai", model, usage)
    return usage


def cache_write_surcharge(provider: str, model: str, cache_write_tokens: int) -> float:
    """Extra USD (on top of the normal input rate already charged by
    ``estimate_cost``) for explicit cache creation, e.g. Anthropic's
    ``cache_creation_input_tokens`` billed at 1.25× input."""
    price = lookup_price(provider, model)
    if price is None or cache_write_tokens <= 0 or price.cache_write is None:
        return 0.0
    return cache_write_tokens * max(0.0, price.cache_write - price.input) / 1_000_000.0
