"""Price table hygiene: provenance and non-zero prices for canonical model ids."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.common import Usage
from codeverse3d.models.pricing import (
    PRICES,
    SOURCES,
    estimate_cost,
    price_provenance,
    unit_prices,
)

#: model ids exposed by defaults, routes, CLI examples, and public backend contracts.
#: They must stay priceable even when no recorded run happens to mention them.
SUPPORTED_MODEL_IDS = (
    "gemini:gemini-3.7-flash",
    "gemini:gemini-3.6-flash",
    "gemini:gemini-3.5-flash",
    "gemini:gemini-3.1-pro-preview",
    "gemini:gemini-3.1-flash-lite",
    "gemini:gemini-3.1-flash-image",
    "gemini:gemini-2.5-flash-image",
    "openai:gpt-5.6-sol",
    "openai:gpt-5.5",
    "openai:gpt-5",
    "anthropic:claude-fable-5",
    "anthropic:claude-opus-5",
    "anthropic:claude-sonnet-5",
    "anthropic:claude-haiku-4-5",
    "anthropic:claude-haiku-4-5-20251001",
    "anthropic:claude-opus-5[1m]",  # what the DEFAULT claude-code arm reports (CC-2)
)


def test_long_context_tier_is_applied():
    small = Usage(input_tokens=100_000, output_tokens=1_000)
    big = Usage(input_tokens=300_000, output_tokens=1_000)
    assert unit_prices("gemini", "gemini-3.1-pro-preview", prompt_tokens=100_000)[0] == 2.0
    assert unit_prices("gemini", "gemini-3.1-pro-preview", prompt_tokens=300_000)[0] == 4.0
    assert estimate_cost("gemini", "gemini-3.1-pro-preview", big) > \
        3 * estimate_cost("gemini", "gemini-3.1-pro-preview", small)
    # flash has no long-context tier
    assert unit_prices("gemini", "gemini-3.7-flash", prompt_tokens=900_000)[0] == 0.75


def test_supported_model_ids_resolve_to_a_price():
    u = Usage(input_tokens=1_000, output_tokens=1_000)
    for model_id in SUPPORTED_MODEL_IDS:
        provider, model = model_id.split(":", 1)
        assert price_provenance(provider, model).price is not None, model_id
        assert estimate_cost(provider, model, u) > 0, model_id


def test_the_bracketed_context_variant_prices_on_its_own_row():
    """A bracketed context variant requires its own exact price row."""
    row = price_provenance("anthropic", "claude-opus-5[1m]")
    assert row.match == "exact" and row.price is not None
    assert row.status == "inferred" and row.checked >= "2026-08-24"
    u = Usage(input_tokens=2, output_tokens=46_921)
    assert estimate_cost("anthropic", "claude-opus-5[1m]", u) == pytest.approx(
        estimate_cost("anthropic", "claude-opus-5", u)), "same rates as the standard-context row"
    # a bracketed suffix still must not borrow a sibling's price by accident
    assert price_provenance("anthropic", "claude-sonnet-5[1m]").price is None


# --------------------------------------------------------------- staleness maintenance
def test_every_price_row_was_checked_within_the_maintenance_window():
    """When this fails, re-check the providers' pages and bump CHECKED — not the threshold."""
    from datetime import date, datetime

    from codeverse3d.cli.cost_cmd import STALE_AFTER_DAYS
    from codeverse3d.models.pricing import price_provenance

    stale = []
    for prov, model in sorted(PRICES):
        row = price_provenance(prov, model)
        assert row.status in ("verified", "inferred", "unverified"), model
        assert (PRICES[(prov, model)].source or prov) in SOURCES, model
        try:
            age = (date.today() - datetime.strptime(row.checked, "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            stale.append(f"{prov}:{model} (no checked date)")
            continue
        if age > STALE_AFTER_DAYS:
            stale.append(f"{prov}:{model} ({age} days)")
    assert not stale, f"price rows older than {STALE_AFTER_DAYS} days: {stale}"
