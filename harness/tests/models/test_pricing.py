"""pricing.estimate_cost + lookup."""

from __future__ import annotations

import logging

import pytest

from codeverse3d.contracts.common import Usage
from codeverse3d.models.pricing import PRICES, estimate_cost, lookup_price, unit_prices


def test_astra_prices_cached_input_reasoning_and_its_strict_long_prompt_tier():
    """A live codex pilot ran gpt-6-astra unpriced (2026-09-23)."""
    usage = Usage(input_tokens=100_000, cached_tokens=80_000, output_tokens=4_000, thoughts_tokens=6_000)
    assert estimate_cost("openai", "gpt-6-astra", usage) == pytest.approx(.78)
    assert unit_prices("openai", "gpt-6-astra", prompt_tokens=272_000) == (10, 1, 50)
    assert unit_prices("openai", "gpt-6-astra", prompt_tokens=272_001) == (20, 2, 75)
    # haiku-5.5 switches the whole request at 100k prompt tokens
    assert unit_prices("anthropic", "claude-haiku-5-5", prompt_tokens=100_000) == (0.10, 0.01, 0.50)
    assert unit_prices("anthropic", "claude-haiku-5-5", prompt_tokens=100_001) == (0.50, 0.05, 2.50)


@pytest.mark.parametrize(
    ("provider", "model", "row"),
    [
        ("anthropic", "claude-opus-5-5-20260301", "claude-opus-5-5"),
        ("gemini", "gemini-3.8-flash-preview-09", "gemini-3.8-flash"),
        ("gemini", "gemini-3.8-flash-medium", "gemini-3.8-flash"),
    ],
)
def test_a_dated_or_tagged_id_prices_on_its_family_row(provider, model, row):
    assert lookup_price(provider, model) is PRICES[(provider, row)]


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-55"),
        ("anthropic", "claude-opus-5-5-turbo"),
    ],
)
def test_a_different_family_is_unpriced_and_warned(provider, model, caplog):
    caplog.set_level(logging.WARNING)
    assert lookup_price(provider, model) is None
    assert estimate_cost(provider, model, Usage(input_tokens=10, output_tokens=10)) == 0.0
    assert any(model in r.message for r in caplog.records)
