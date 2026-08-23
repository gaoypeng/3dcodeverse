"""USD price table (per 1M tokens) + ``estimate_cost``.

Providers never return USD, so every ``Usage`` is priced here.  Prices are the
latest published rates known to the author at the time of writing (2026-08);
entries marked *approximate* were taken from secondary sources or inferred
from the closest sibling model — refresh when the official pages change.

Only the standard (short-context) tier is modelled; long-context surcharges
and cache *storage* fees are not.  Unknown models price at 0.0 and log a
warning once per (provider, model).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from codeverse.contracts.common import Usage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens.  ``cached`` = cache-read input; ``cache_write`` = cache
    creation (Anthropic/OpenAI explicit caching); ``thoughts`` defaults to output."""

    input: float
    output: float
    cached: float = 0.0
    cache_write: float | None = None
    approximate: bool = False


# fmt: off
PRICES: dict[tuple[str, str], Price] = {
    # ---------------------------------------------------------------- gemini
    # 3.6 / 3.7 flash: introductory rates valid through 2026-12-31; they double
    # on 2027-01-01 ($1.50 / $7.50 / $0.15).  Update then.
    ("gemini", "gemini-3.7-flash"):           Price(0.75, 3.75, 0.075),
    ("gemini", "gemini-3.6-flash"):           Price(0.75, 3.75, 0.075),
    ("gemini", "gemini-3.5-flash"):           Price(1.50, 9.00, 0.15),
    ("gemini", "gemini-3.1-pro-preview"):     Price(2.00, 12.00, 0.20),
    ("gemini", "gemini-3.1-flash-lite"):      Price(0.25, 1.50, 0.025),
    ("gemini", "gemini-3.1-flash-image"):     Price(0.25, 1.50, 0.025, approximate=True),  # + per-image fees
    ("gemini", "gemini-3-pro-preview"):       Price(2.00, 12.00, 0.20),
    ("gemini", "gemini-3-flash-preview"):     Price(0.50, 3.00, 0.05),
    ("gemini", "gemini-2.5-pro"):             Price(1.25, 10.00, 0.125),
    ("gemini", "gemini-2.5-flash"):           Price(0.30, 2.50, 0.075),
    ("gemini", "gemini-2.5-flash-lite"):      Price(0.10, 0.40, 0.025),
    # (texturing) image-output model: output tokens ARE the image (~1.3k tokens ≈ $0.039 / image);
    # models/gemini_image.py additionally floors cost at IMAGE_USD per image.  approximate.
    ("gemini", "gemini-2.5-flash-image"):     Price(0.30, 30.00, 0.075, approximate=True),
    # ------------------------------------------------------------- anthropic
    ("anthropic", "claude-fable-5"):          Price(10.00, 50.00, 1.00, cache_write=12.50),
    ("anthropic", "claude-opus-5"):           Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-8"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-7"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-6"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-5"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-1"):         Price(15.00, 75.00, 1.50, cache_write=18.75),
    ("anthropic", "claude-opus-4"):           Price(15.00, 75.00, 1.50, cache_write=18.75),
    # sonnet-5 standard rate; an intro rate ($2 / $10) applies through 2026-08-31
    ("anthropic", "claude-sonnet-5"):         Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-sonnet-4-6"):       Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-sonnet-4-5"):       Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-sonnet-4"):         Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-haiku-4-5"):        Price(1.00, 5.00, 0.10, cache_write=1.25),
    ("anthropic", "claude-haiku-4"):          Price(1.00, 5.00, 0.10, cache_write=1.25, approximate=True),
    # ---------------------------------------------------------------- openai
    ("openai", "gpt-5.6-sol"):                Price(5.00, 30.00, 0.50, cache_write=6.25, approximate=True),
    ("openai", "gpt-5.6"):                    Price(5.00, 30.00, 0.50, approximate=True),
    ("openai", "gpt-5.5"):                    Price(5.00, 30.00, 0.50, approximate=True),
    ("openai", "gpt-5.4"):                    Price(2.50, 15.00, 0.25, approximate=True),
    ("openai", "gpt-5.3-codex"):              Price(1.75, 14.00, 0.175, approximate=True),
    ("openai", "gpt-5.2-codex"):              Price(1.75, 14.00, 0.175, approximate=True),
    ("openai", "gpt-5.2"):                    Price(1.75, 14.00, 0.175, approximate=True),
    ("openai", "gpt-5.1-codex"):              Price(1.25, 10.00, 0.125),
    ("openai", "gpt-5.1"):                    Price(1.25, 10.00, 0.125),
    ("openai", "gpt-5-codex"):                Price(1.25, 10.00, 0.125),
    ("openai", "gpt-5-mini"):                 Price(0.25, 2.00, 0.025),
    ("openai", "gpt-5-nano"):                 Price(0.05, 0.40, 0.005),
    ("openai", "gpt-5"):                      Price(1.25, 10.00, 0.125),
    ("openai", "gpt-4.1"):                    Price(2.00, 8.00, 0.50),
    ("openai", "gpt-4.1-mini"):               Price(0.40, 1.60, 0.10),
    ("openai", "gpt-4.1-nano"):               Price(0.10, 0.40, 0.025),
    ("openai", "gpt-4o"):                     Price(2.50, 10.00, 1.25),
    ("openai", "gpt-4o-mini"):                Price(0.15, 0.60, 0.075),
    ("openai", "o3"):                         Price(2.00, 8.00, 0.50),
    ("openai", "o3-mini"):                    Price(1.10, 4.40, 0.55),
    ("openai", "o3-pro"):                     Price(20.00, 80.00),
    ("openai", "o4-mini"):                    Price(1.10, 4.40, 0.275),
    ("openai", "o1"):                         Price(15.00, 60.00, 7.50),
    ("openai", "o1-mini"):                    Price(1.10, 4.40, 0.55),
}
# fmt: on

_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


def _normalise(model: str) -> str:
    return model.strip().lower()


# words that may follow a priced model id without changing which model it is
_VERSION_WORDS = frozenset({"latest", "preview", "exp", "beta", "alpha", "stable", "snapshot"})


def _is_version_suffix(rest: str) -> bool:
    """True when ``rest`` (what follows a table key in a model id) is only a
    version / date / channel tag — ``-20260301``, ``@001``, ``-preview-05-20``,
    ``-latest`` — and not a sibling model name (``-nano``, ``-mini``, ``-codex``,
    ``.5``), which must price separately or be reported unknown."""
    if not rest or rest[0] not in "-_@:":
        return False
    segments = rest[1:].split("-")
    return all(seg.isdigit() or seg in _VERSION_WORDS for seg in segments)


def lookup_price(provider: str, model: str) -> Price | None:
    """Exact match first, then the longest table key that ``model`` extends by a
    pure version/date suffix (``claude-opus-5-20260301``, ``gemini-3.7-flash-preview-09``).
    A different family name after the key (``gpt-4.1-nano`` vs ``gpt-4.1``) is NOT a
    match: unknown → ``None`` so the caller prices 0.0 and warns, never a sibling's rate."""
    m = _normalise(model)
    exact = PRICES.get((provider, m))
    if exact is not None:
        return exact
    best: tuple[int, Price] | None = None
    for (prov, key), price in PRICES.items():
        if prov != provider or not m.startswith(key) or not _is_version_suffix(m[len(key) :]):
            continue
        if best is None or len(key) > best[0]:
            best = (len(key), price)
    return best[1] if best else None


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
    cached = max(0, min(usage.cached_tokens, usage.input_tokens))
    uncached = max(0, usage.input_tokens - cached)
    cost = uncached * price.input / m
    cost += cached * price.cached / m
    cost += (usage.output_tokens + usage.thoughts_tokens) * price.output / m
    return cost


def cache_write_surcharge(provider: str, model: str, cache_write_tokens: int) -> float:
    """Extra USD (on top of the normal input rate already charged by
    ``estimate_cost``) for explicit cache creation, e.g. Anthropic's
    ``cache_creation_input_tokens`` billed at 1.25× input."""
    price = lookup_price(provider, model)
    if price is None or cache_write_tokens <= 0 or price.cache_write is None:
        return 0.0
    return cache_write_tokens * max(0.0, price.cache_write - price.input) / 1_000_000.0
