"""USD price table (per 1M tokens) + ``estimate_cost`` + price provenance.

Every ``Usage`` is priced here (providers never return USD).  Each row carries a
:class:`Provenance` entry — where the number came from and when it was last
checked — so a cost report can say *which* price row produced a dollar and
whether it was verified or merely inferred (``3dcode cost prices``).

Only the standard (short-context) tier is modelled, **except** the documented
>200k-prompt tiers of the Gemini pro models, which ``estimate_cost`` applies
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

from codeverse.contracts.common import Usage

log = logging.getLogger(__name__)

#: date the tables below were last reconciled against the providers' pricing pages
CHECKED = "2026-08-23"

#: source ids used by :data:`PROVENANCE`
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
    """USD per 1M tokens.  ``cached`` = cache-read input; ``cache_write`` = cache
    creation (Anthropic explicit caching, 5-minute TTL); ``thoughts`` are billed
    at the output rate.  ``image_usd`` = per generated 1024² image for
    image-output models (their image tokens are billed per image, not per token).
    """

    input: float
    output: float
    cached: float = 0.0
    cache_write: float | None = None
    approximate: bool = False
    image_usd: float = 0.0
    long_context: LongContext | None = None


@dataclass(frozen=True)
class Provenance:
    """Where one price row came from.  ``status``:

    * ``verified``   — read off the provider's public pricing page on ``checked``
    * ``inferred``   — no published row; copied from the nearest sibling
    * ``unverified`` — the model is not listed on the page (retired / alias / CLI-only)
    """

    source: str
    checked: str = CHECKED
    status: str = "verified"
    note: str = ""

    @property
    def url(self) -> str:
        return SOURCES.get(self.source, self.source)


_V = Provenance  # verified rows read straight off a pricing page


# fmt: off
PRICES: dict[tuple[str, str], Price] = {
    # ---------------------------------------------------------------- gemini
    # 3.6 / 3.7 / 3.8 flash: introductory rates valid through 2026-12-31; they double
    # on 2027-01-01 ($1.50 / $7.50 / $0.15).  Update then.  No >200k surcharge.
    ("gemini", "gemini-3.8-flash"):           Price(0.75, 3.75, 0.075),
    ("gemini", "gemini-3.7-flash"):           Price(0.75, 3.75, 0.075),
    ("gemini", "gemini-3.6-flash"):           Price(0.75, 3.75, 0.075),
    ("gemini", "gemini-3.5-flash"):           Price(1.50, 9.00, 0.15),
    ("gemini", "gemini-3.1-pro-preview"):     Price(2.00, 12.00, 0.20,
                                                    long_context=LongContext(200_000, 4.00, 18.00, 0.40)),
    ("gemini", "gemini-3.1-flash-lite"):      Price(0.25, 1.50, 0.025),
    # image-output model: text/thinking output $3/M, IMAGE output $60/M ≈ $0.067
    # per 1K image (1290 tokens); 0.5K $0.045, 2K $0.134, 4K $0.151.  No cache row.
    ("gemini", "gemini-3.1-flash-image"):     Price(0.50, 3.00, 0.05, image_usd=0.067),
    ("gemini", "gemini-3-pro-preview"):       Price(2.00, 12.00, 0.20,
                                                    long_context=LongContext(200_000, 4.00, 18.00, 0.40)),
    ("gemini", "gemini-3-flash-preview"):     Price(0.50, 3.00, 0.05),
    ("gemini", "gemini-2.5-pro"):             Price(1.25, 10.00, 0.125,
                                                    long_context=LongContext(200_000, 2.50, 15.00, 0.25)),
    ("gemini", "gemini-2.5-flash"):           Price(0.30, 2.50, 0.03),
    ("gemini", "gemini-2.5-flash-lite"):      Price(0.10, 0.40, 0.01),
    # image tokens are billed per image: 1024² = 1290 tokens = $0.039 ⇒ $30.23/M
    ("gemini", "gemini-2.5-flash-image"):     Price(0.30, 30.00, 0.03, image_usd=0.039),
    # ------------------------------------------------------------- anthropic
    ("anthropic", "claude-fable-5"):          Price(10.00, 50.00, 1.00, cache_write=12.50),
    ("anthropic", "claude-mythos-5"):         Price(10.00, 50.00, 1.00, cache_write=12.50),
    ("anthropic", "claude-opus-5"):           Price(5.00, 25.00, 0.50, cache_write=6.25),
    # the 1M-context variant of opus-5, and the id the DEFAULT claude-code arm serves.
    # "[1m]" is not a version suffix, so it cannot prefix-match claude-opus-5 and priced
    # at $0.00 until 2026-08-24 (a recorded run billed $1.218 under this exact key).
    ("anthropic", "claude-opus-5[1m]"):       Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-8"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-7"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-6"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-5"):         Price(5.00, 25.00, 0.50, cache_write=6.25),
    ("anthropic", "claude-opus-4-1"):         Price(15.00, 75.00, 1.50, cache_write=18.75),
    ("anthropic", "claude-opus-4"):           Price(15.00, 75.00, 1.50, cache_write=18.75),
    # sonnet-5: the $2/$10 launch rate became the standard rate (the 2026-09-01
    # increase to $3/$15 was cancelled) — was priced 50% too high here until 2026-08-23.
    ("anthropic", "claude-sonnet-5"):         Price(2.00, 10.00, 0.20, cache_write=2.50),
    ("anthropic", "claude-sonnet-4-6"):       Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-sonnet-4-5"):       Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-sonnet-4"):         Price(3.00, 15.00, 0.30, cache_write=3.75),
    ("anthropic", "claude-haiku-4-5"):        Price(1.00, 5.00, 0.10, cache_write=1.25),
    ("anthropic", "claude-haiku-4"):          Price(1.00, 5.00, 0.10, cache_write=1.25, approximate=True),
    ("anthropic", "claude-haiku-3-5"):        Price(0.80, 4.00, 0.08, cache_write=1.00),
    # ---------------------------------------------------------------- openai
    # gpt-5.6-sol is CHEAPER than gpt-5.6 (was priced as its equal until 2026-08-23).
    ("openai", "gpt-5.6-sol"):                Price(4.00, 20.00, 0.40),
    ("openai", "gpt-5.6-terra"):              Price(2.00, 12.00, 0.20),
    ("openai", "gpt-5.6-luna"):               Price(0.20, 1.20, 0.02),
    ("openai", "gpt-5.6"):                    Price(5.00, 30.00, 0.50),
    ("openai", "gpt-5.5"):                    Price(5.00, 30.00, 0.50),
    ("openai", "gpt-5.4"):                    Price(2.50, 15.00, 0.25),
    ("openai", "gpt-5.3-codex"):              Price(1.75, 14.00, 0.175),
    ("openai", "gpt-5.2-codex"):              Price(1.75, 14.00, 0.175, approximate=True),
    ("openai", "gpt-5.2"):                    Price(1.75, 14.00, 0.175),
    ("openai", "gpt-5.1-codex"):              Price(1.25, 10.00, 0.125, approximate=True),
    ("openai", "gpt-5.1"):                    Price(1.25, 10.00, 0.125),
    ("openai", "gpt-5-codex"):                Price(1.25, 10.00, 0.125, approximate=True),
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
    ("openai", "o1-mini"):                    Price(1.10, 4.40, 0.55, approximate=True),
}

#: per price row: where the number came from and when it was last checked.
#: Every key of :data:`PRICES` must appear here (``tests/cost/test_price_hygiene.py``).
PROVENANCE: dict[tuple[str, str], Provenance] = {
    ("gemini", "gemini-3.8-flash"):       _V("gemini", checked="2026-09-21",
                                              note="intro rate through 2026-12-31, then 1.50/7.50/0.15"),
    ("gemini", "gemini-3.7-flash"):       _V("gemini", note="intro rate through 2026-12-31, then 1.50/7.50/0.15"),
    ("gemini", "gemini-3.6-flash"):       _V("gemini", note="intro rate through 2026-12-31"),
    ("gemini", "gemini-3.5-flash"):       _V("gemini"),
    ("gemini", "gemini-3.1-pro-preview"): _V("gemini", note=">200k prompts: 4.00/18.00/0.40 (modelled)"),
    ("gemini", "gemini-3.1-flash-lite"):  _V("gemini", note="audio input is 2x; text/image/video modelled"),
    ("gemini", "gemini-3.1-flash-image"): _V("gemini", note="image output $60/M ≈ $0.067 per 1K image; 0.5K $0.045, 4K $0.151"),
    ("gemini", "gemini-3-pro-preview"):   _V("gemini", note=">200k prompts: 4.00/18.00 (modelled)"),
    ("gemini", "gemini-3-flash-preview"): _V("gemini"),
    ("gemini", "gemini-2.5-pro"):         _V("gemini", note=">200k prompts: 2.50/15.00/0.25 (modelled)"),
    ("gemini", "gemini-2.5-flash"):       _V("gemini", note="cached was 0.075 here until 2026-08-23; published 0.03"),
    ("gemini", "gemini-2.5-flash-lite"):  _V("gemini", note="cached was 0.025 here until 2026-08-23; published 0.01"),
    ("gemini", "gemini-2.5-flash-image"): _V("gemini", status="inferred",
                                             note="output/M derived from $0.039 per 1290-token 1024² image"),
    ("anthropic", "claude-fable-5"):      _V("anthropic", note="1h cache write 20.00 (not modelled)"),
    ("anthropic", "claude-mythos-5"):     _V("anthropic", note="limited availability"),
    ("anthropic", "claude-opus-5"):       _V("anthropic", note="fast mode is 10.00/50.00 (not modelled)"),
    ("anthropic", "claude-opus-5[1m]"):   _V("provider-cost", checked="2026-08-24", status="inferred",
                                             note="1M-context opus-5, served by the default claude-code arm.  A probe "
                                                  "billed costUSD 0.034620 for in=2, out=4, 1h-cache-write=3451 — "
                                                  "exactly 2x5.00 + 4x25.00 + 3451x10.00 per 1M, i.e. the standard "
                                                  "opus-5 rates with the 1h cache write at 2x input (not modelled here, "
                                                  "as for every other Anthropic row)"),
    ("anthropic", "claude-opus-4-8"):     _V("anthropic", note="fast mode is 10.00/50.00 (not modelled)"),
    ("anthropic", "claude-opus-4-7"):     _V("anthropic"),
    ("anthropic", "claude-opus-4-6"):     _V("anthropic"),
    ("anthropic", "claude-opus-4-5"):     _V("anthropic"),
    ("anthropic", "claude-opus-4-1"):     _V("anthropic", note="retired except on Bedrock / Google Cloud"),
    ("anthropic", "claude-opus-4"):       _V("anthropic", note="retired except on Google Cloud"),
    ("anthropic", "claude-sonnet-5"):     _V("anthropic", note="launch rate 2.00/10.00 is now standard; was 3.00/15.00 here"),
    ("anthropic", "claude-sonnet-4-6"):   _V("anthropic"),
    ("anthropic", "claude-sonnet-4-5"):   _V("anthropic"),
    ("anthropic", "claude-sonnet-4"):     _V("anthropic", note="retired except on Bedrock / Google Cloud"),
    ("anthropic", "claude-haiku-4-5"):    _V("anthropic"),
    ("anthropic", "claude-haiku-4"):      _V("inferred", status="unverified",
                                             note="not listed on the pricing page; assumed = haiku-4.5"),
    ("anthropic", "claude-haiku-3-5"):    _V("anthropic", note="retired except on Bedrock / Google Cloud"),
    ("openai", "gpt-5.6-sol"):            _V("openai", note="4.00/20.00/0.40; was 5.00/30.00/0.50 here until 2026-08-23"),
    ("openai", "gpt-5.6-terra"):          _V("openai", checked="2026-08-24",
                                             note="mid tier; fast mode is 4.00/24.00 (not modelled)"),
    ("openai", "gpt-5.6-luna"):           _V("openai", checked="2026-08-24",
                                             note="small tier, cut 80% on 2026-07-30; fast mode is 0.40/2.40 (not modelled)"),
    ("openai", "gpt-5.6"):                _V("openai"),
    ("openai", "gpt-5.5"):                _V("openai"),
    ("openai", "gpt-5.4"):                _V("openai"),
    ("openai", "gpt-5.3-codex"):          _V("openai"),
    ("openai", "gpt-5.2-codex"):          _V("inferred", status="inferred", note="not listed; assumed = gpt-5.2"),
    ("openai", "gpt-5.2"):                _V("openai"),
    ("openai", "gpt-5.1-codex"):          _V("inferred", status="inferred", note="not listed; assumed = gpt-5.1"),
    ("openai", "gpt-5.1"):                _V("openai"),
    ("openai", "gpt-5-codex"):            _V("inferred", status="inferred", note="not listed; assumed = gpt-5"),
    ("openai", "gpt-5-mini"):             _V("openai"),
    ("openai", "gpt-5-nano"):             _V("openai"),
    ("openai", "gpt-5"):                  _V("openai"),
    ("openai", "gpt-4.1"):                _V("openai"),
    ("openai", "gpt-4.1-mini"):           _V("openai"),
    ("openai", "gpt-4.1-nano"):           _V("openai"),
    ("openai", "gpt-4o"):                 _V("openai"),
    ("openai", "gpt-4o-mini"):            _V("openai"),
    ("openai", "o3"):                     _V("openai"),
    ("openai", "o3-mini"):                _V("openai"),
    ("openai", "o3-pro"):                 _V("openai", note="no cached-input rate published"),
    ("openai", "o4-mini"):                _V("openai"),
    ("openai", "o1"):                     _V("openai"),
    ("openai", "o1-mini"):                _V("inferred", status="unverified", note="not listed; kept at the o3-mini rate"),
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
    A different family name after the key (``gpt-4.1-nano`` vs ``gpt-4.1``) is NOT a
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
    provenance: Provenance | None

    @property
    def approximate(self) -> bool:
        """True when the dollar cannot be trusted to the cent: no row, an
        inferred/unverified row, or a row flagged ``approximate``."""
        if self.price is None or self.provenance is None:
            return True
        return self.price.approximate or self.provenance.status != "verified"

    @property
    def source(self) -> str:
        return self.provenance.url if self.provenance else "unknown"

    @property
    def checked(self) -> str:
        return self.provenance.checked if self.provenance else ""

    @property
    def status(self) -> str:
        if self.price is None:
            return "unknown"
        return self.provenance.status if self.provenance else "unverified"


def price_provenance(provider: str, model: str) -> PriceRow:
    """Resolve ``provider:model`` to a price **and its source**.  Never raises;
    an unknown model comes back as ``match="unknown"``, ``price=None``."""
    prov = (provider or "").strip().lower()
    found = _match(prov, model)
    if not found:
        return PriceRow(prov, _normalise(model), "", "unknown", None, None)
    key, price = found
    match = "exact" if key[1] == _normalise(model) else "prefix"
    return PriceRow(prov, _normalise(model), key[1], match, price, PROVENANCE.get(key))


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


def cache_write_surcharge(provider: str, model: str, cache_write_tokens: int) -> float:
    """Extra USD (on top of the normal input rate already charged by
    ``estimate_cost``) for explicit cache creation, e.g. Anthropic's
    ``cache_creation_input_tokens`` billed at 1.25× input."""
    price = lookup_price(provider, model)
    if price is None or cache_write_tokens <= 0 or price.cache_write is None:
        return 0.0
    return cache_write_tokens * max(0.0, price.cache_write - price.input) / 1_000_000.0
