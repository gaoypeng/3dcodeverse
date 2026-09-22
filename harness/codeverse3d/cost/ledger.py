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
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from codeverse3d.contracts.common import Usage
from codeverse3d.cost.context import CallContext, _stage, attribute
from codeverse3d.cost.tally import Tally, book_usage
from codeverse3d.cost.types import (
    CallCost,
    CostBucket,
    Role,
    Stage,
    Summary,
    normalise_ids,
    role_for_stage,
)
from codeverse3d.models.pricing import (
    cache_write_surcharge,
    estimate_cost,
    price_provenance,
    unit_prices,
)
from codeverse3d.proc import iter_jsonl_lines

log = logging.getLogger(__name__)

#: the ledger of a run lives in its telemetry bucket (docs/RUN_LAYOUT.md §telemetry) — the
#: one name it has: the root ``cost_ledger.jsonl`` and ``telemetry/usage.jsonl`` aliases went
#: on 2026-09-22 (runs written before keep them as dangling-safe symlinks nobody reads)
TELEMETRY_DIR = "telemetry"
TELEMETRY_LEDGER = f"{TELEMETRY_DIR}/cost.jsonl"

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


_fallback_lock = threading.Lock()
_fallback: CostLedger | None = None

#: sentinel: "this execution context did not state a ledger"
_UNSET: Any = object()
#: context-local override of the default ledger.  Context-local ONLY, like
#: ``codeverse3d.cost.context._run_var``: a bench worker running its own prompt in its
#: own thread must not have its rows land in a sibling run's file, and a process-wide
#: copy of it could not unwind correctly when two runs overlap (2026-08-30).
_default_var: ContextVar[Any] = ContextVar("c3d_cost_default_ledger", default=_UNSET)


@contextmanager
def bound_ledger(path: str | Path) -> Iterator[CostLedger]:
    """Point :func:`record_call` at ``path`` for the duration of the block.

    Unwinds with the ``ContextVar`` token, so nested and parallel blocks each undo
    exactly their own binding."""
    led = CostLedger(path)
    token = _default_var.set(led)
    try:
        yield led
    finally:
        _default_var.reset(token)


def default_ledger() -> CostLedger:
    """The ledger :func:`record_call` writes to when no ``ledger=`` is given: whatever
    :func:`bound_ledger` bound here, else the per-process fallback log
    (:func:`process_ledger_path`), so a call made outside any run — ``3dcode judge``, a
    bench script, a notebook — is still accounted for.  There is no off switch: the
    ledger is the only record of money (``C3D_COST_LEDGER``, which read ``off`` or a path —
    and, as a Settings field, made every command die on anything but a boolean — went
    2026-09-22)."""
    global _fallback
    led = _default_var.get()
    if led is not _UNSET:
        return led
    with _fallback_lock:
        if _fallback is None:
            _fallback = CostLedger(process_ledger_path())
        return _fallback


def process_ledger_path() -> Path:
    """The fallback log for calls made with no run context: one file per process
    under ``<cache_dir>/cost/``.  Never inside a run directory, so it can never be
    mistaken for a run's own ledger."""
    try:
        from codeverse3d.config import get_settings

        base = Path(get_settings().cache_dir)
    except Exception:  # pragma: no cover - settings must never break accounting
        base = Path.home() / ".cache" / "codeverse3d"
    stamp = time.strftime("%Y%m%d", time.localtime())
    return base / "cost" / f"p{os.getpid()}-{stamp}.jsonl"


def existing_ledger_path(workspace: str | Path) -> Path | None:
    """The ledger file a run actually has, or ``None``: ``telemetry/cost.jsonl``, the only one
    read (a walk of every run under ``$HOME`` on 2026-08-30 found 677 root ``cost_ledger.jsonl``
    files, every one a symlink to it)."""
    p = Path(workspace) / TELEMETRY_LEDGER
    return p if p.is_file() else None


def open_run_ledger(workspace: str | Path) -> CostLedger:
    """The run's ledger, ``telemetry/cost.jsonl``, ready to append to (the file is created by
    its first row)."""
    return CostLedger(Path(workspace) / TELEMETRY_LEDGER)


#: context._stage is the same function; ledger already imports from context.  NOT true
#: of the neighbouring _as_role — that one returns Role.OTHER where context._role returns
#: None, and Role.OTHER is truthy, so it survives CallContext.resolved().
_as_stage = _stage


def _as_role(value: Role | str | None) -> Role | None:
    if value is None or isinstance(value, Role):
        return value
    try:
        return Role(value)
    except ValueError:
        return Role.OTHER


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
    stage: Stage | str | None = None,
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
    tallies: tuple[Tally, ...] | None = None,
    **extra: Any,
) -> CallCost:
    """Append one priced call to the ledger, book it into the open tallies
    (``cost.tally`` — or ``tallies``, captured by a caller whose row lands later in a
    thread with no context), and return the row.

    Callers pass the ``Usage`` they already hold::

        from codeverse3d.cost import record_call
        record_call(res.usage, run=ws.slug, round=idx, stage="baseline",
                    role="generator", ledger=ws.root / "cost_ledger.jsonl")

    Anything the caller leaves out (``run`` / ``round`` / ``stage`` / ``role``) is
    resolved from the ambient run context and the call label — see
    :mod:`codeverse3d.cost.context`.

    Never raises: a failure to account is logged, not propagated."""
    u = usage or Usage()
    ctx = attribute(CallContext(run=run, round=round, stage=_as_stage(stage), role=_as_role(role),
                                label=label), label=label)
    st = ctx.stage or Stage.OTHER
    rl = ctx.role or role_for_stage(st)
    run, round, label = ctx.run, ctx.round, ctx.label  # noqa: A001 - see the signature
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
    (ledger if isinstance(ledger, CostLedger) else CostLedger(ledger) if ledger else default_ledger()).append(row)
    if source != "attempt":  # an attempt row's tokens are already on its call's own row
        book_usage(Usage(backend=row.backend, model=row.model, input_tokens=row.input_tokens,
                         output_tokens=row.output_tokens, cached_tokens=row.cached_tokens,
                         thoughts_tokens=row.thoughts_tokens, tool_calls=row.tool_calls,
                         cost_usd=row.cost_usd, latency_ms=row.latency_ms), tallies)
    return row


def load_ledger(path: str | Path, *, include_attempts: bool = False) -> list[CallCost]:
    """Read a ledger file, or a run directory holding one (``telemetry/cost.jsonl``).
    Bad lines are skipped with a debug log — a truncated last line never loses the
    rest of the file.

    ``source="attempt"`` rows (one per round-trip, ``instrument.MeteredChatModel``)
    are left out unless ``include_attempts=True``: their tokens are already on the
    call's logical row, so every aggregate built on this reader (``summarise``,
    ``reconstruct``, the CLI) keeps counting each call exactly once.  This is the ONLY
    place that filter lives — ``summarise`` used to repeat it, which made
    ``include_attempts=True`` summarise to $0.  Paid-but-discarded round-trips are
    ``source="extra"`` instead and always count: nothing else records them."""
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
        if not include_attempts and row.source == "attempt":
            continue
        rows.append(row)
    return rows


def _key(row: CallCost, dim: str) -> str:
    if dim == "round":
        return "-" if row.round is None else f"r{row.round:02d}"
    value = getattr(row, dim, "")
    return str(value) if value != "" else "(none)"


def ledger_usage(rows: Iterable[CallCost]) -> Usage:
    """The money and tokens of ``rows`` as one ``Usage`` — how a run's ``total_usage`` is read
    off its ledger (list price; attempt rows are already out: ``load_ledger``)."""
    t = summarise(rows, dimensions=()).total
    return Usage(input_tokens=t.input_tokens, output_tokens=t.output_tokens, cached_tokens=t.cached_tokens,
                 thoughts_tokens=t.thoughts_tokens, tool_calls=t.tool_calls, cost_usd=t.cost_usd,
                 latency_ms=t.latency_ms)


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
