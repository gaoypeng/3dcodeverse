"""Price table hygiene: provenance and non-zero prices for canonical model ids."""

from __future__ import annotations

from codeverse3d.contracts.common import Usage
from codeverse3d.models.pricing import (
    PRICES,
    estimate_cost,
    price_provenance,
)

#: model ids exposed by defaults, routes, CLI examples, and public backend contracts.
#: They must stay priceable even when no recorded run happens to mention them.
SUPPORTED_MODEL_IDS = (
    "gemini:gemini-3.8-flash",
    "gemini:gemini-3.1-pro-preview",
    "gemini:gemini-4-argon",
    "openai:gpt-6-astra",
    "openai:gpt-6.1-sol",
    "openai:gpt-6-sol",
    "openai:gpt-6-luna",
    "anthropic:claude-fable-5-1",
    "anthropic:claude-opus-5-5",  # what the DEFAULT claude-code arm reports (live, 2026-10-13)
    "anthropic:claude-sonnet-5-5",
    "anthropic:claude-haiku-5-5",
    "anthropic:claude-haiku-4-5",
    "anthropic:claude-haiku-4-5-20251001",
)


def test_supported_model_ids_resolve_to_a_price():
    u = Usage(input_tokens=1_000, output_tokens=1_000)
    for model_id in SUPPORTED_MODEL_IDS:
        provider, model = model_id.split(":", 1)
        assert price_provenance(provider, model).price is not None, model_id
        assert estimate_cost(provider, model, u) > 0, model_id


def test_every_price_row_declares_its_provenance():
    for prov, model in sorted(PRICES):
        assert price_provenance(prov, model).status in ("verified", "inferred", "unverified"), model
