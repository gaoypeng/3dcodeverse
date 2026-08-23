"""pricing.estimate_cost + lookup."""

from __future__ import annotations

import logging

from codeverse.contracts.common import Usage
from codeverse.models.pricing import PRICES, cache_write_surcharge, estimate_cost, lookup_price


def test_known_models_present():
    for prov, m in [
        ("gemini", "gemini-3.7-flash"),
        ("gemini", "gemini-3.6-flash"),
        ("gemini", "gemini-3.5-flash"),
        ("gemini", "gemini-3.1-pro-preview"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("anthropic", "claude-opus-5"),
        ("anthropic", "claude-sonnet-5"),
        ("anthropic", "claude-haiku-4-5"),
        ("openai", "gpt-5.6-sol"),
        ("openai", "gpt-5.5"),
        ("openai", "gpt-5"),
    ]:
        assert (prov, m) in PRICES, m


def test_gemini_cost_math():
    u = Usage(
        input_tokens=1_000_000, cached_tokens=400_000, output_tokens=100_000, thoughts_tokens=50_000
    )
    # 600k*0.75 + 400k*0.075 + 150k*3.75 (per 1M)
    assert abs(estimate_cost("gemini", "gemini-3.7-flash", u) - (0.45 + 0.03 + 0.5625)) < 1e-9


def test_prefix_match_and_boundary():
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
