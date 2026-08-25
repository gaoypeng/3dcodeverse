"""Price table hygiene: provenance for every row, and no silent $0 for any model
id that appears in a recorded run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.contracts.common import Usage
from codeverse.models.pricing import (
    IMAGE_USD_BY_SIZE,
    PRICES,
    PROVENANCE,
    estimate_cost,
    per_image_usd,
    price_provenance,
    unit_prices,
)

REPO = Path(__file__).resolve().parents[2]

#: model ids the harness uses by default (config/contracts defaults + the CLI
#: backends we run here) — asserted even when no recorded run is present.
CORE_MODEL_IDS = (
    "gemini:gemini-3.7-flash",
    "gemini:gemini-3.1-pro-preview",
    "gemini:gemini-3.1-flash-image",
    "gemini:gemini-2.5-flash-image",
    "openai:gpt-5.6-sol",
    "anthropic:claude-fable-5",
    "anthropic:claude-haiku-4-5-20251001",
    "anthropic:claude-opus-5[1m]",  # what the DEFAULT claude-code arm reports (CC-2)
)


def test_every_price_row_has_provenance():
    assert set(PRICES) == set(PROVENANCE), (
        f"missing provenance: {sorted(set(PRICES) - set(PROVENANCE))}; "
        f"stale provenance: {sorted(set(PROVENANCE) - set(PRICES))}")
    for key, prov in PROVENANCE.items():
        assert prov.checked and prov.checked[:2] == "20", key
        assert prov.status in ("verified", "inferred", "unverified"), key
        assert prov.url, key


def test_provenance_flags_the_rows_we_cannot_stand_behind():
    for key, price in PRICES.items():
        row = price_provenance(*key)
        if price.approximate:
            assert row.approximate, key
    assert price_provenance("gemini", "gemini-3.7-flash").approximate is False
    assert price_provenance("anthropic", "claude-haiku-4").approximate is True
    assert price_provenance("gemini", "not-a-model").status == "unknown"


def test_image_models_have_a_per_image_price():
    for provider, model in (("gemini", "gemini-3.1-flash-image"), ("gemini", "gemini-2.5-flash-image")):
        assert PRICES[(provider, model)].image_usd > 0, model
        assert per_image_usd(provider, model) == PRICES[(provider, model)].image_usd
        assert per_image_usd(provider, model, size=512) > 0
        assert (provider, model) in IMAGE_USD_BY_SIZE
    # version suffixes resolve too (the image model is often pinned with -preview)
    assert per_image_usd("gemini", "gemini-2.5-flash-image-preview") == 0.039


def test_image_price_agrees_with_the_image_backend():
    """``models/gemini_image.IMAGE_USD`` and the price table must not drift apart."""
    from codeverse.models.gemini_image import IMAGE_USD

    for model, usd in IMAGE_USD.items():
        assert per_image_usd("gemini", model) == pytest.approx(usd), model


def test_long_context_tier_is_applied():
    small = Usage(input_tokens=100_000, output_tokens=1_000)
    big = Usage(input_tokens=300_000, output_tokens=1_000)
    assert unit_prices("gemini", "gemini-3.1-pro-preview", prompt_tokens=100_000)[0] == 2.0
    assert unit_prices("gemini", "gemini-3.1-pro-preview", prompt_tokens=300_000)[0] == 4.0
    assert estimate_cost("gemini", "gemini-3.1-pro-preview", big) > \
        3 * estimate_cost("gemini", "gemini-3.1-pro-preview", small)
    # flash has no long-context tier
    assert unit_prices("gemini", "gemini-3.7-flash", prompt_tokens=900_000)[0] == 0.75


def _recorded_model_ids() -> set[tuple[str, str]]:
    """Every (backend, model) pair that appears in a recorded run of this repo."""
    from codeverse.cost.types import split_model_id

    out: set[tuple[str, str]] = set()
    for root in (REPO / "runs", REPO / "bench" / "out"):
        if not root.is_dir():
            continue
        for record in root.rglob("record.json"):
            try:
                data = json.loads(record.read_text())
            except (OSError, ValueError):
                continue
            usages = [data.get("total_usage")]
            for rnd in data.get("rounds") or []:
                usages.append(rnd.get("usage"))
                usages.append((rnd.get("judgment") or {}).get("usage"))
            for u in usages:
                if isinstance(u, dict) and (u.get("model") or ""):
                    out.add(split_model_id(str(u.get("backend") or ""), str(u["model"])))
    return out


@pytest.mark.parametrize("model_id", CORE_MODEL_IDS)
def test_core_model_ids_resolve_to_a_price(model_id: str):
    provider, model = model_id.split(":", 1)
    u = Usage(input_tokens=1_000, output_tokens=1_000)
    assert price_provenance(provider, model).price is not None, model_id
    assert estimate_cost(provider, model, u) > 0, model_id


def test_the_bracketed_context_variant_prices_on_its_own_row():
    """CC-2: '[1m]' is not a version/date/channel tag, so _is_version_suffix() refuses to
    fall back to claude-opus-5 — correctly, since a sibling's rate is never assumed here.
    The id is real (the default claude-code arm reports it, and one recorded cell billed
    $1.218 under it), so it needs a row of its own or `3dcv cost prices` calls the default
    arm unknown and estimate_cost values it $0.00."""
    row = price_provenance("anthropic", "claude-opus-5[1m]")
    assert row.match == "exact" and row.price is not None
    assert row.provenance.status == "inferred" and row.provenance.checked >= "2026-08-24"
    u = Usage(input_tokens=2, output_tokens=46_921)
    assert estimate_cost("anthropic", "claude-opus-5[1m]", u) == pytest.approx(
        estimate_cost("anthropic", "claude-opus-5", u)), "same rates as the standard-context row"
    # a bracketed suffix still must not borrow a sibling's price by accident
    assert price_provenance("anthropic", "claude-sonnet-5[1m]").price is None


def test_every_recorded_model_id_resolves_to_a_price():
    """No silent $0: a model that billed us in a recorded run must have a row."""
    unknown = sorted(f"{p}:{m}" for p, m in _recorded_model_ids()
                     if p and price_provenance(p, m).price is None)
    assert not unknown, f"models with no price row (they cost $0 in every report): {unknown}"


# --------------------------------------------------------------- staleness maintenance
def test_every_price_row_was_checked_within_the_maintenance_window():
    """A price nobody re-checked for 90 days is not evidence.  When this fails,
    re-read the providers' pricing pages, update PRICES/PROVENANCE and bump
    ``CHECKED`` — do not raise the threshold."""
    from datetime import date, datetime

    from codeverse.cli.cost_cmd import STALE_AFTER_DAYS
    from codeverse.models.pricing import PRICES, price_provenance

    stale = []
    for prov, model in sorted(PRICES):
        row = price_provenance(prov, model)
        try:
            age = (date.today() - datetime.strptime(row.checked, "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            stale.append(f"{prov}:{model} (no checked date)")
            continue
        if age > STALE_AFTER_DAYS:
            stale.append(f"{prov}:{model} ({age} days)")
    assert not stale, f"price rows older than {STALE_AFTER_DAYS} days: {stale}"


def test_a_ledger_row_carries_the_provenance_of_the_price_it_used(tmp_path):
    """The audit trail: which row produced this dollar, and can we stand behind it."""
    from codeverse.contracts.common import Usage as U
    from codeverse.cost import record_call

    row = record_call(U(backend="gemini", model="gemini-3.1-pro-preview", input_tokens=1_000,
                        output_tokens=100), run="r", stage="judge", ledger=tmp_path / "l.jsonl")
    assert row.price_source == "exact" and row.price_approximate is False
    assert row.price_checked and row.price_input == 2.0 and row.price_output == 12.0
    unknown = record_call(U(backend="gemini", model="gemini-9.9-imaginary", input_tokens=1_000),
                          run="r", stage="judge", ledger=tmp_path / "l.jsonl")
    assert unknown.price_source == "unknown" and unknown.price_approximate is True
