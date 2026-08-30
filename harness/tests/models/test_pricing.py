"""pricing.estimate_cost + lookup."""

from __future__ import annotations

import logging

from codeverse.contracts.common import Usage
from codeverse.models.pricing import PRICES, cache_write_surcharge, estimate_cost, lookup_price


def test_gemini_cost_math():
    u = Usage(
        input_tokens=1_000_000, cached_tokens=400_000, output_tokens=100_000, thoughts_tokens=50_000
    )
    # 600k*0.75 + 400k*0.075 + 150k*3.75 (per 1M)
    assert abs(estimate_cost("gemini", "gemini-3.7-flash", u) - (0.45 + 0.03 + 0.5625)) < 1e-9


def test_price_lookup_respects_family_boundaries(caplog):
    caplog.set_level(logging.WARNING)
    assert (
        lookup_price("anthropic", "claude-opus-5-20260301")
        is PRICES[("anthropic", "claude-opus-5")]
    )
    assert lookup_price("openai", "gpt-5.5-2026") is PRICES[("openai", "gpt-5.5")]
    assert lookup_price("openai", "gpt-5") is PRICES[("openai", "gpt-5")]
    assert lookup_price("openai", "gpt-55") is None
    assert (
        lookup_price("gemini", "gemini-3.7-flash-preview-09")
        is PRICES[("gemini", "gemini-3.7-flash")]
    )
    assert lookup_price("openai", "gpt-4.1-nano") is PRICES[("openai", "gpt-4.1-nano")]
    assert lookup_price("openai", "o3-mini") is PRICES[("openai", "o3-mini")]
    assert lookup_price("openai", "o3-pro") is PRICES[("openai", "o3-pro")]
    assert lookup_price("openai", "gpt-5-codex") is PRICES[("openai", "gpt-5-codex")]
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert abs(estimate_cost("openai", "gpt-4.1-nano", u) - 0.50) < 1e-9
    for prov, m in [
        ("openai", "gpt-5.4-mini"),
        ("openai", "gpt-5.7"),  # ".7" is a different minor version, not a date tag
        ("gemini", "gemini-3.7-flash-lite"),
        ("gemini", "gemini-2.5-flash-imagine"),
        ("anthropic", "claude-opus-5-turbo"),
    ]:
        assert lookup_price(prov, m) is None, m
        assert estimate_cost(prov, m, u) == 0.0, m
        assert any(m in r.message for r in caplog.records), m
    assert lookup_price("openai", "gpt-4o-2024-08-06") is PRICES[("openai", "gpt-4o")]
    assert (
        lookup_price("gemini", "gemini-2.5-flash-preview-05-20")
        is PRICES[("gemini", "gemini-2.5-flash")]
    )
    assert (
        lookup_price("gemini", "gemini-2.5-flash-image-preview")
        is PRICES[("gemini", "gemini-2.5-flash-image")]
    )
    assert (
        lookup_price("anthropic", "claude-sonnet-5@20260301")
        is PRICES[("anthropic", "claude-sonnet-5")]
    )
    assert (
        lookup_price("gemini", "gemini-3.7-flash-latest") is PRICES[("gemini", "gemini-3.7-flash")]
    )


def test_unknown_model_zero_and_warns_once(caplog):
    caplog.set_level(logging.WARNING)
    u = Usage(input_tokens=10, output_tokens=10)
    assert estimate_cost("gemini", "nope-model-x", u) == 0.0
    assert estimate_cost("gemini", "nope-model-x", u) == 0.0
    assert sum("nope-model-x" in r.message for r in caplog.records) == 1


def test_cache_write_surcharge():
    # opus-5: write 6.25 vs input 5.00 → 1.25 per 1M extra
    assert abs(cache_write_surcharge("anthropic", "claude-opus-5", 1_000_000) - 1.25) < 1e-9
    assert cache_write_surcharge("gemini", "gemini-3.7-flash", 1000) == 0.0
