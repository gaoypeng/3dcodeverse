"""The cost ledger of a finished run (``telemetry/cost.jsonl``): read tolerantly, re-price, bucket by dimension."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from c3d_flywheel.cost.pricing import (
    estimate_cost,
    price_provenance,
    unit_prices,
)
from c3d_flywheel.cost.types import (
    CallCost,
    CostBucket,
    Summary,
    normalise_ids,
)
from c3d_flywheel.legacy import Usage
from c3d_flywheel.runlayout import iter_jsonl_lines

log = logging.getLogger(__name__)

#: a run's ledger lives in ``<run>/telemetry/``
TELEMETRY_DIR = "telemetry"
TELEMETRY_LEDGER = f"{TELEMETRY_DIR}/cost.jsonl"

DIMENSIONS = ("run", "stage", "role", "backend", "model", "provider", "round", "outcome")

#: backends that report their own cost: authoritative, so never re-priced from the token table
PROVIDER_PRICED_BACKENDS = frozenset({"claude-code"})


def existing_ledger_path(workspace: str | Path) -> Path | None:
    """The run's ``telemetry/cost.jsonl`` if it exists, else ``None``."""
    p = Path(workspace) / TELEMETRY_LEDGER
    return p if p.is_file() else None


def price_call(
    usage: Usage,
    *,
    backend: str = "",
    model: str = "",
    reprice: bool = False,
) -> tuple[float, dict[str, Any]]:
    """``(cost_usd, price fields)`` for one call.

    ``reprice=False`` keeps the caller's cost and only records the price row; ``True`` recomputes it."""
    prov_model = model or usage.model
    kind, provider, name = normalise_ids(backend or usage.backend, prov_model)
    row = price_provenance(provider, name)
    p_in, p_cached, p_out = unit_prices(provider, name, prompt_tokens=usage.input_tokens)
    cost = float(usage.cost_usd)
    if kind in PROVIDER_PRICED_BACKENDS and cost:
        return cost, {"backend": kind, "provider": provider, "model": name, "model_id": prov_model,
                      "price_input": 0.0, "price_cached": 0.0, "price_output": 0.0,
                      "price_source": "provider-reported", "price_approximate": True,
                      "price_checked": row.checked}
    if reprice or not cost:
        cost = estimate_cost(provider, name, usage)
    source = row.match if row.price is not None else "unknown"
    if row.price is None and float(usage.cost_usd) > 0:
        source = "provider-reported"  # the backend billed us; our table cannot explain it
    fields = {
        "backend": kind,
        "provider": provider,
        "model": name,
        "model_id": prov_model,
        "price_input": p_in,
        "price_cached": p_cached,
        "price_output": p_out,
        "price_source": source if source == "unknown" else (f"prefix:{row.key}" if source == "prefix" else source),
        "price_approximate": row.approximate,
        "price_checked": row.checked,
    }
    return cost, fields


def load_ledger(path: str | Path) -> list[CallCost]:
    """Read a ledger file, or a run directory holding one; bad lines are skipped."""
    p = Path(path)
    if p.is_dir():
        found = existing_ledger_path(p)
        if found is None:
            return []
        p = found
    rows: list[CallCost] = []
    for i, line in iter_jsonl_lines(p):
        try:
            row = CallCost.model_validate_json(line)
        except Exception as e:  # pragma: no cover - defensive
            log.debug("cost ledger %s:%d unreadable: %s", p, i, e)
            continue
        rows.append(row)
    return rows


def _key(row: CallCost, dim: str) -> str:
    if dim == "round":
        return "-" if row.round is None else f"r{row.round:02d}"
    value = getattr(row, dim, "")
    return str(value) if value != "" else "(none)"


def ledger_usage(rows: Iterable[CallCost]) -> Usage:
    """The money and tokens of ``rows`` as one ``Usage`` (list price)."""
    t = summarise(rows, dimensions=()).total
    return Usage(input_tokens=t.input_tokens, output_tokens=t.output_tokens, cached_tokens=t.cached_tokens,
                 thoughts_tokens=t.thoughts_tokens, tool_calls=t.tool_calls, cost_usd=t.cost_usd,
                 latency_ms=t.latency_ms)


def summarise(rows: Iterable[CallCost], *, dimensions: Sequence[str] = DIMENSIONS) -> Summary:
    """Total + per-dimension buckets (``dimensions`` may name any scalar ``CallCost`` field)."""
    out = Summary()
    for row in rows:
        out.total.add(row)
        for dim in dimensions:
            bucket = out.by.setdefault(dim, {}).setdefault(_key(row, dim), CostBucket(key=_key(row, dim)))
            bucket.add(row)
    return out
