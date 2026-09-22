"""Meter every model call and every agent session into the ledger.

Two thin proxies do the whole job:

* :class:`MeteredChatModel` wraps a :class:`~codeverse3d.models.base.ChatModel`.
  Every ``generate`` appends one :class:`~codeverse3d.cost.types.CallCost` row
  (tokens, the unit prices actually used + their provenance, $, latency,
  cache hit, outcome).  Because ``models.registry.get_chat_model`` returns the
  proxy, this covers the planner, the judges, the captioner and single-shot
  generation.
* :class:`MeteredAgent` wraps a :class:`~codeverse3d.agents.registry.CodingAgent`.
  It sets the ambient attribution (round / stage / label) for the session so
  the model rows above land in the right bucket and, because every backend is a
  vendor CLI whose calls we cannot see, records one session row from
  ``AgentResult.usage``.  ``agents.registry
  .get_coding_agent`` returns the proxy.

Accounting is never allowed to fail a run: every hook is wrapped, and an
exception in the ledger is logged and swallowed.  The proxies forward every
other attribute, so a caller that reaches for ``model.pool`` or ``agent.kind``
sees the real object's.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.chat import ChatRequest, ChatResponse
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.context import (
    AttemptRecord,
    CallContext,
    attempt_recording,
    attribute,
    bound_run,
    call_context,
    run_binding,
)
from codeverse3d.cost.ledger import (
    CostLedger,
    bound_ledger,
    default_ledger,
    existing_ledger_path,
    open_run_ledger,
    record_call,
)
from codeverse3d.cost.tally import Tally, book_time, open_tallies
from codeverse3d.cost.types import Role, Stage, stage_for_label

log = logging.getLogger(__name__)


class MeteredChatModel:
    """ChatModel proxy that appends one ledger row per call."""

    def __init__(self, inner: Any):
        self._inner = inner

    # -- protocol ---------------------------------------------------------
    @property
    def provider(self) -> str:
        return getattr(self._inner, "provider", "")

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "")

    @property
    def id(self) -> str:
        return str(self._inner.id)

    def __getattr__(self, name: str) -> Any:  # everything else is the real model's
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MeteredChatModel({self._inner!r})"

    # -- the meter --------------------------------------------------------
    def generate(self, request: ChatRequest) -> ChatResponse:
        call_id = uuid4().hex  # joins the per-attempt rows to the call's logical row
        # captured NOW: a hedge loser lands after this call returned, in a thread with
        # no context — neither the run's ledger, its name, the stage/role/round
        # attribution (the loser's row used to fall back to the bare label) nor the
        # open tallies are reachable
        ledger = default_ledger()
        run = run_binding().run
        ctx = attribute(label=request.label)
        tallies = open_tallies()
        booked: list[int] = []  # round-trips that already billed themselves as "extra"
        trips = _RoundTrips()

        def attempt_row(rec: AttemptRecord) -> None:
            trips.add(rec)
            if self._record_attempt(request, call_id, rec, ledger, run, ctx, tallies):
                booked.append(rec.attempt)

        t0 = time.perf_counter()
        try:
            with attempt_recording(attempt_row):
                resp = self._inner.generate(request)
        except BaseException as exc:  # noqa: BLE001 - record the attempt, then re-raise
            # a failed call may still have been billed: ModelError.usage carries what the
            # provider charged (a bad-JSON reply costs like a good one) — but only when
            # the round-trips did not already book it, or the last one counts twice
            ms = int((time.perf_counter() - t0) * 1000)
            usage = getattr(exc, "usage", None)
            usage = usage if isinstance(usage, Usage) else Usage(backend=self.provider, model=self.model)
            book_time(ms / 1000, trips.lost_ms(ms, usage.latency_ms) / 1000)
            if booked:
                usage = Usage(backend=usage.backend, model=usage.model, latency_ms=usage.latency_ms)
            self._record(usage, request, outcome=_outcome(exc), ms=ms,
                         attempts=getattr(exc, "attempts", 0), call_id=call_id)
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        book_time(ms / 1000, trips.lost_ms(ms, resp.usage.latency_ms) / 1000)
        # which key served it and how many round-trips it took (gemini.py puts both in
        # ``raw``); until 2026-08-26 no telemetry row carried either, so the per-key
        # distribution of calls could only be probed, never read
        self._record(resp.usage, request, outcome="ok", ms=ms,
                     key=resp.raw.get("key"), attempts=resp.raw.get("attempts", 0),
                     call_id=call_id)
        return resp

    def _record(self, usage: Usage, request: ChatRequest, *, outcome: str, ms: int,
                key: object = None, attempts: object = 0, call_id: str = "") -> None:
        try:
            record_call(usage, label=request.label, backend=usage.backend or self.provider,
                        model=usage.model or self.model, outcome=outcome,
                        latency_ms=usage.latency_ms or ms,
                        key=_key_suffix(key), attempts=int(attempts or 0), call_id=call_id)
        except Exception as e:  # pragma: no cover - accounting must never break a call
            log.debug("cost: could not record %s: %s", request.label, e)

    def _record_attempt(self, request: ChatRequest, call_id: str, rec: AttemptRecord,
                        ledger: CostLedger | None, run: str = "",
                        ctx: CallContext | None = None, tallies: tuple[Tally, ...] | None = None) -> bool:
        """One row per round-trip.  A round-trip that was DISCARDED and still cost money
        (a billed-but-invalid reply, a hedge loser that landed) is money nothing else
        records, so it goes in as ``source="extra"`` and counts in every total; the rest
        are ``source="attempt"`` forensics, excluded because the call's logical row
        already carries their tokens.  Returns True when it billed an ``extra``."""
        extra = bool(rec.discarded and rec.usage.cost_usd)
        try:
            record_call(rec.usage, run=run, label=request.label,
                        stage=ctx.stage if ctx else None, role=ctx.role if ctx else None,
                        round=ctx.round if ctx else None,
                        backend=rec.usage.backend or self.provider,
                        model=rec.usage.model or self.model,
                        outcome="discarded" if rec.discarded else "ok",
                        latency_ms=rec.usage.latency_ms,
                        source="extra" if extra else "attempt", key=_key_suffix(rec.key),
                        call_id=call_id, attempt=rec.attempt, discarded=rec.discarded,
                        ledger=ledger, tallies=tallies)
        except Exception as e:  # pragma: no cover - accounting must never break a call
            log.debug("cost: could not record attempt %s#%d: %s", request.label, rec.attempt, e)
            return False
        return extra


class _RoundTrips:
    """The round-trips of ONE logical call, as the backend reported them through the attempt
    sink (``models.retry.rotate_with_retries``), reduced to what the minutes need: how long
    the ones that got an answer took, and whether any failed without one."""

    def __init__(self) -> None:
        self.answered_ms = 0  # the winner, and a reply that came back unusable (bad JSON, empty)
        self.failed = False  # a round-trip with no answer at all: 503 / 429 / timeout / transport / dead key
        self.seen = False

    def add(self, rec: AttemptRecord) -> None:
        self.seen = True
        if rec.discarded and rec.outcome == "ok":
            return  # a hedge loser that answered ran beside the winner, not before it
        if rec.usage.latency_ms:
            self.answered_ms += rec.usage.latency_ms
        elif rec.outcome != "ok":
            self.failed = True

    def lost_ms(self, wall_ms: int, answer_ms: int) -> int:
        """The call's milliseconds lost to provider errors: everything outside its answered
        round-trips once one failed without an answer — its failed tries, their back-off and
        the key waits between them.  A backend that reports no round-trips (the SDK adapters)
        is charged everything outside the answer it returned."""
        if not self.seen:
            return max(0, wall_ms - answer_ms)
        return max(0, wall_ms - self.answered_ms) if self.failed else 0


def _key_suffix(key: object) -> str:
    """The last 4 chars of an API key as ``…ab12`` — the ledger never holds more."""
    s = str(key or "").strip()
    return f"…{s[-4:]}" if s else ""


def _outcome(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    return "error"


class MeteredAgent:
    """CodingAgent proxy: attribution for the session + its one ledger row.  The
    job runs as given — the turn cap is decided in ``tracks.generation``."""

    def __init__(self, inner: Any):
        self._inner = inner

    @property
    def kind(self) -> str:
        return str(getattr(self._inner, "kind", ""))

    @property
    def model(self) -> str:
        return str(getattr(self._inner, "model", ""))

    @property
    def id(self) -> str:
        return str(self._inner.id)

    def available(self) -> tuple[bool, str]:
        return self._inner.available()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MeteredAgent({self._inner!r})"

    def run(self, job: AgentJob) -> AgentResult:
        stage = stage_for_label(job.kind or job.label)
        t0 = time.perf_counter()
        with call_context(round=job.round, stage=stage, role=Role.GENERATOR, label=job.label):
            result = self._inner.run(job)
        wall_s = time.perf_counter() - t0
        # the CLI's own retries of 503 / 429 / token-limit errors, read from its log
        book_time(wall_s, float(result.provider_wait_s or 0.0))
        # an opaque CLI: its session row is the only record
        self._record_session(job, result, stage, int(wall_s * 1000))
        return result

    def _record_session(self, job: AgentJob, result: AgentResult, stage: Stage, ms: int) -> None:
        usage = result.usage
        if not (usage.input_tokens or usage.output_tokens or usage.cost_usd):
            return
        try:
            record_call(usage, round=job.round, stage=stage, role=Role.GENERATOR, label=job.label,
                        backend=usage.backend or self.kind, model=usage.model or self.model,
                        outcome="ok" if result.ok else (result.exit_reason or "error"),
                        latency_ms=usage.latency_ms or ms,
                        n_calls=max(1, int(result.tool_calls or 1)), source="session")
        except Exception as e:  # pragma: no cover
            log.debug("cost: could not record agent session %s: %s", job.label, e)


# --------------------------------------------------------------------------- factories
def metered_chat_model(model: Any) -> Any:
    """Wrap ``model`` unless it is already metered.  Metering cannot be switched off: the
    ledger is the only record of money (the ``C3D_COST_LEDGER=off`` switch went 2026-09-22)."""
    if model is None or isinstance(model, MeteredChatModel):
        return model
    return MeteredChatModel(model)


def metered_agent(agent: Any) -> Any:
    if agent is None or isinstance(agent, MeteredAgent):
        return agent
    return MeteredAgent(agent)


# --------------------------------------------------------------------------- run activation
@contextmanager
def run_ledger(workspace: str | Path, *, run: str = "", create: bool = True) -> Iterator[CostLedger | None]:
    """Meter one run into ``<workspace>/telemetry/cost.jsonl``.

    Binds the run name and points :func:`~codeverse3d.cost.ledger.record_call` at the
    run's ledger for the duration of the block.

    ``create=False`` is for work done *after* a run finished (``3dcode judge``,
    a post-hoc texture pass): it appends only when the run already keeps a
    ledger, because a ledger holding nothing but the re-judge would be read as
    the whole run's cost and hide everything the run really spent.  Without one
    the rows go to the per-process log instead.

    **Nests and parallelises.**  Both bindings unwind by ``ContextVar`` token, so a
    bench cell that opens a ledger for the cell and then a second one for the
    harness run inside it keeps both, and ``bench.run_bench`` can run N prompts in
    N threads without their rows mixing — a save-and-restore by value could not,
    because a fresh worker saved whatever a sibling had published last."""
    ws = Path(workspace)
    if not create and existing_ledger_path(ws) is None:
        with bound_run(run or ws.name):
            yield None
        return
    ledger = open_run_ledger(ws)
    with bound_run(run or ws.name), bound_ledger(ledger.path):
        yield ledger


__all__ = ["MeteredAgent", "MeteredChatModel", "metered_agent", "metered_chat_model", "run_ledger"]
