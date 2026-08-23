"""Append-only cost ledger: one JSONL row per model / agent call.

Writer (``record_call``) — callers hand it the ``Usage`` they already have; the
ledger resolves the price row, records the unit prices it used and their
provenance, and appends one line.  It never raises and never blocks a run:
accounting must not be able to fail a build.

Reader (``load_ledger``) tolerates rows written by older/newer versions
(unknown fields are kept, missing fields default).

Aggregator (``summarise``) buckets rows by any of ``run / stage / role /
backend / model / provider / round / outcome``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from codeverse.contracts.common import Usage
from codeverse.cost.types import (
    CallCost,
    CostBucket,
    Role,
    Stage,
    Summary,
    normalise_ids,
    stage_for_label,
)
from codeverse.models.pricing import (
    cache_write_surcharge,
    estimate_cost,
    price_provenance,
    unit_prices,
)

log = logging.getLogger(__name__)

#: default ledger file inside a run workspace
LEDGER_NAME = "cost_ledger.jsonl"

#: env var that points ``record_call`` at a ledger when no path is passed
LEDGER_ENV = "CV3D_COST_LEDGER"

DIMENSIONS = ("run", "stage", "role", "backend", "model", "provider", "round", "outcome")

#: backends that bill us themselves (a subscription CLI reporting ``total_cost_usd``).
#: Their dollar is authoritative and their reported model id is not — claude-code
#: names one of the served models, not the one that did the work — so these rows
#: are never re-priced from the token table.
PROVIDER_PRICED_BACKENDS = frozenset({"claude-code"})


class CostLedger:
    """Append-only JSONL ledger.  Thread-safe; one line per call, flushed on write."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, row: CallCost) -> CallCost:
        line = json.dumps(row.model_dump(mode="json"), ensure_ascii=False, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        except OSError as e:  # pragma: no cover - disk full / read-only run dir
            log.warning("cost ledger append failed (%s): %s", self.path, e)
        return row

    def read(self) -> list[CallCost]:
        return load_ledger(self.path)

    def summarise(self, **kw: Any) -> Summary:
        return summarise(self.read(), **kw)


_default_lock = threading.Lock()
_default: CostLedger | None = None


def set_default_ledger(path: str | Path | None) -> CostLedger | None:
    """Point the module-level :func:`record_call` at ``path`` (``None`` disables it)."""
    global _default
    with _default_lock:
        _default = CostLedger(path) if path is not None else None
        return _default


def default_ledger() -> CostLedger | None:
    """The ledger :func:`record_call` writes to when no ``ledger=`` is given:
    whatever :func:`set_default_ledger` set, else ``$CV3D_COST_LEDGER``."""
    global _default
    with _default_lock:
        if _default is not None:
            return _default
        env = os.environ.get(LEDGER_ENV, "").strip()
        if env:
            _default = CostLedger(env)
        return _default


def ledger_path(workspace: str | Path) -> Path:
    """The conventional ledger path of a run workspace (``<ws>/cost_ledger.jsonl``)."""
    return Path(workspace) / LEDGER_NAME


def price_call(
    usage: Usage,
    *,
    backend: str = "",
    model: str = "",
    cache_write_tokens: int = 0,
    reprice: bool = False,
) -> tuple[float, dict[str, Any]]:
    """``(cost_usd, price fields)`` for one call.

    ``reprice=False`` keeps the cost the caller already computed (that is what
    was actually billed at the time) and only *records* which price row would
    explain it; ``reprice=True`` recomputes from the current table — how a cost
    audit re-values an old run."""
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
        if cache_write_tokens:
            cost += cache_write_surcharge(provider, name, cache_write_tokens)
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


def record_call(
    usage: Usage | None = None,
    *,
    run: str = "",
    stage: Stage | str = Stage.OTHER,
    role: Role | str | None = None,
    round: int | None = None,  # noqa: A002 - matches the record/round vocabulary
    label: str = "",
    backend: str = "",
    model: str = "",
    outcome: str = "ok",
    latency_ms: int | None = None,
    cache_write_tokens: int = 0,
    n_calls: int = 1,
    source: str = "live",
    ledger: CostLedger | str | Path | None = None,
    ts: float | None = None,
    reprice: bool = False,
    **extra: Any,
) -> CallCost:
    """Append one priced call to the ledger and return the row.

    Callers pass the ``Usage`` they already hold::

        from codeverse.cost import record_call
        record_call(res.usage, run=ws.slug, round=idx, stage="baseline",
                    role="generator", ledger=ws.root / "cost_ledger.jsonl")

    Never raises: a failure to account is logged, not propagated."""
    u = usage or Usage()
    try:
        st = Stage(stage) if not isinstance(stage, Stage) else stage
    except ValueError:
        st = stage_for_label(str(stage))
    if role is None:
        from codeverse.cost.types import role_for_stage

        rl = role_for_stage(st)
    else:
        try:
            rl = Role(role) if not isinstance(role, Role) else role
        except ValueError:
            rl = Role.OTHER
    try:
        cost, price_fields = price_call(u, backend=backend, model=model,
                                        cache_write_tokens=cache_write_tokens, reprice=reprice)
    except Exception as e:  # pragma: no cover - pricing must never break a run
        log.warning("cost: pricing failed for %s/%s: %s", backend, model, e)
        cost, price_fields = float(u.cost_usd), {}
    row = CallCost(
        ts=ts if ts is not None else time.time(),
        run=run,
        round=round,
        stage=st,
        role=rl,
        label=label,
        input_tokens=int(u.input_tokens),
        cached_tokens=int(u.cached_tokens),
        output_tokens=int(u.output_tokens),
        thoughts_tokens=int(u.thoughts_tokens),
        cache_write_tokens=int(cache_write_tokens),
        tool_calls=int(u.tool_calls),
        cost_usd=cost,
        recorded_usd=float(u.cost_usd),
        latency_ms=int(u.latency_ms if latency_ms is None else latency_ms),
        cache_hit=int(u.cached_tokens) > 0,
        outcome=outcome,
        n_calls=max(1, int(n_calls)),
        source=source,
        **price_fields,
        **extra,
    )
    target = ledger if isinstance(ledger, CostLedger) else (CostLedger(ledger) if ledger else default_ledger())
    if target is not None:
        target.append(row)
    return row


def load_ledger(path: str | Path) -> list[CallCost]:
    """Read a ledger file (or a directory containing one).  Bad lines are skipped
    with a debug log — a truncated last line never loses the rest of the file."""
    p = Path(path)
    if p.is_dir():
        p = p / LEDGER_NAME
    if not p.is_file():
        return []
    rows: list[CallCost] = []
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if not line.strip():
            continue
        try:
            rows.append(CallCost.model_validate_json(line))
        except Exception as e:  # pragma: no cover - defensive
            log.debug("cost ledger %s:%d unreadable: %s", p, i + 1, e)
    return rows


def _key(row: CallCost, dim: str) -> str:
    if dim == "round":
        return "-" if row.round is None else f"r{row.round:02d}"
    value = getattr(row, dim, "")
    return str(value) if value != "" else "(none)"


def summarise(rows: Iterable[CallCost], *, dimensions: Sequence[str] = DIMENSIONS) -> Summary:
    """Total + per-dimension buckets.  ``dimensions`` may name any scalar field
    of :class:`CallCost` (defaults to :data:`DIMENSIONS`)."""
    out = Summary()
    for row in rows:
        out.total.add(row)
        for dim in dimensions:
            bucket = out.by.setdefault(dim, {}).setdefault(_key(row, dim), CostBucket(key=_key(row, dim)))
            bucket.add(row)
    return out


def totals_by(rows: Iterable[CallCost], dim: str) -> dict[str, CostBucket]:
    return summarise(rows, dimensions=(dim,)).dimension(dim)
