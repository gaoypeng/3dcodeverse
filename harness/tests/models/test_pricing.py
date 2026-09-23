"""pricing.estimate_cost + lookup."""

from __future__ import annotations

import logging

import pytest

from codeverse3d.contracts.common import Usage
from codeverse3d.models.pricing import PRICES, estimate_cost, lookup_price, unit_prices


def test_gemini_cost_math():
    u = Usage(
        input_tokens=1_000_000, cached_tokens=400_000, output_tokens=100_000, thoughts_tokens=50_000
    )
    # 600k*0.75 + 400k*0.075 + 150k*3.75 (per 1M)
    assert abs(estimate_cost("gemini", "gemini-3.7-flash", u) - (0.45 + 0.03 + 0.5625)) < 1e-9


def test_astra_prices_cached_input_reasoning_and_its_strict_long_prompt_tier():
    """A live codex pilot ran gpt-6-astra unpriced (2026-09-23)."""
    usage = Usage(input_tokens=100_000, cached_tokens=80_000, output_tokens=4_000, thoughts_tokens=6_000)
    assert estimate_cost("openai", "gpt-6-astra", usage) == pytest.approx(.78)
    assert unit_prices("openai", "gpt-6-astra", prompt_tokens=272_000) == (10, 1, 50)
    assert unit_prices("openai", "gpt-6-astra", prompt_tokens=272_001) == (20, 2, 75)


@pytest.mark.parametrize(
    ("provider", "model", "row"),
    [
        ("anthropic", "claude-opus-5-20260301", "claude-opus-5"),
        ("anthropic", "claude-sonnet-5@20260301", "claude-sonnet-5"),
        ("openai", "gpt-5.5-2026", "gpt-5.5"),
        ("openai", "gpt-5", "gpt-5"),
        ("openai", "gpt-4o-2024-08-06", "gpt-4o"),
        ("gemini", "gemini-3.7-flash-preview-09", "gemini-3.7-flash"),
        ("gemini", "gemini-3.7-flash-latest", "gemini-3.7-flash"),
        ("gemini", "gemini-2.5-flash-image-preview", "gemini-2.5-flash-image"),
        # agy's effort-suffixed ids (every agy session was priced $0 until 2026-09-23)
        ("gemini", "gemini-3.7-flash-medium", "gemini-3.7-flash"),
        ("gemini", "gemini-3.7-flash-high", "gemini-3.7-flash"),
        ("openai", "gpt-6-astra-2026-09-01", "gpt-6-astra"),
    ],
)
def test_a_dated_or_tagged_id_prices_on_its_family_row(provider, model, row):
    assert lookup_price(provider, model) is PRICES[(provider, row)]


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-55"),
        ("openai", "gpt-5.7"),  # ".7" is a different minor version, not a date tag
        ("gemini", "gemini-3.7-flash-lite"),
        ("anthropic", "claude-opus-5-turbo"),
        ("openai", "gpt-6-astra-mini"),
    ],
)
def test_a_different_family_is_unpriced_and_warned(provider, model, caplog):
    caplog.set_level(logging.WARNING)
    assert lookup_price(provider, model) is None
    assert estimate_cost(provider, model, Usage(input_tokens=10, output_tokens=10)) == 0.0
    assert any(model in r.message for r in caplog.records)
