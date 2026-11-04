"""Meter every model call and agent session into the ledger.

:class:`MeteredChatModel` appends one ``CallCost`` row per ``generate``; :class:`MeteredAgent`
sets the attribution for a session and records its one session row.  Accounting never fails
a run: ledger errors are logged and swallowed, and other attributes forward to the real object.
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


class _Proxy:

    def __init__(self, inner: Any):
        self._inner = inner

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "")

    @property
    def id(self) -> str:
        return str(self._inner.id)

    def __getattr__(self, name: str) -> Any:  # everything else is the real object's
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self._inner!r})"


class MeteredChatModel(_Proxy):

    @property
    def provider(self) -> str:
        return getattr(self._inner, "provider", "")

    # -- the meter --------------------------------------------------------
    def generate(self, request: ChatRequest) -> ChatResponse:
        call_id = uuid4().hex  # joins the per-attempt rows to the call's logical row
        # captured now: a hedge loser lands after this call returned, in a thread with no context
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
            # a failed call may still have been billed (ModelError.usage), unless a round-trip already booked it
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
        """Book a discarded round-trip that still cost money as ``source="extra"``; True when it did."""
        if not (rec.discarded and rec.usage.cost_usd):
            return False
        try:
            record_call(rec.usage, run=run, label=request.label,
                        stage=ctx.stage if ctx else None, role=ctx.role if ctx else None,
                        round=ctx.round if ctx else None,
                        backend=rec.usage.backend or self.provider,
                        model=rec.usage.model or self.model,
                        outcome="discarded" if rec.discarded else "ok",
                        latency_ms=rec.usage.latency_ms,
                        source="extra", key=_key_suffix(rec.key),
                        call_id=call_id, attempt=rec.attempt, discarded=rec.discarded,
                        ledger=ledger, tallies=tallies)
        except Exception as e:  # pragma: no cover - accounting must never break a call
            log.debug("cost: could not record attempt %s#%d: %s", request.label, rec.attempt, e)
            return False
        return True


class _RoundTrips:
    """The round-trips of one logical call, reduced to what the minutes need: time answered, any failure."""

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
        """Milliseconds lost to provider errors: everything outside the answered round-trips once one failed."""
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


class MeteredAgent(_Proxy):
    """CodingAgent proxy: attribution for the session + its one ledger row."""

    @property
    def kind(self) -> str:
        return str(getattr(self._inner, "kind", ""))

    def run(self, job: AgentJob) -> AgentResult:
        stage = stage_for_label(job.kind or job.label)
        t0 = time.perf_counter()
        with call_context(round=job.round, stage=stage, role=Role.GENERATOR, label=job.label):
            result = self._inner.run(job)
        wall_s = time.perf_counter() - t0
        book_time(wall_s, float(result.provider_wait_s or 0.0))
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
                        n_calls=max(1, int(result.tool_calls or 1)),
                        source="estimate" if result.usage_estimated else "session",
                        **({"effort": result.effort} if result.effort else {}))
        except Exception as e:  # pragma: no cover
            log.debug("cost: could not record agent session %s: %s", job.label, e)


# --------------------------------------------------------------------------- factories
def metered_chat_model(model: Any) -> Any:
    """Wrap ``model`` unless it is already metered.  Metering cannot be switched off."""
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

    ``create=False`` (work after a run finished) appends only to an existing ledger, else rows go
    to the per-process log, so a re-judge never masquerades as the run's whole cost.
    Nests and parallelises: bindings unwind by ``ContextVar`` token."""
    ws = Path(workspace)
    if not create and existing_ledger_path(ws) is None:
        with bound_run(run or ws.name):
            yield None
        return
    ledger = open_run_ledger(ws)
    with bound_run(run or ws.name), bound_ledger(ledger.path):
        yield ledger


__all__ = ["MeteredAgent", "MeteredChatModel", "metered_agent", "metered_chat_model", "run_ledger"]
