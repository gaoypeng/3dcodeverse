"""Meter every model call and every agent session into the ledger.

Two thin proxies do the whole job:

* :class:`MeteredChatModel` wraps a :class:`~codeverse.models.base.ChatModel`.
  Every ``generate`` appends one :class:`~codeverse.cost.types.CallCost` row
  (tokens, the unit prices actually used + their provenance, $, latency,
  cache hit, outcome).  Because ``models.registry.get_chat_model`` returns the
  proxy, this covers the planner, the judges, the captioner, single-shot
  generation **and every turn of the in-process api-agent**.
* :class:`MeteredAgent` wraps a :class:`~codeverse.agents.base.CodingAgent`.
  It sets the ambient attribution (round / stage / label) for the session so
  the model rows above land in the right bucket, applies the profile's turn cap
  and, for backends whose calls we cannot see (the subscription CLIs — see
  :data:`IN_PROCESS_AGENT_KINDS`), records one session row from
  ``AgentResult.usage``.

Accounting is never allowed to fail a run: every hook is wrapped, and an
exception in the ledger is logged and swallowed.  The proxies forward every
other attribute, so a caller that reaches for ``model.pool`` or ``agent.kind``
sees the real object's.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Usage
from codeverse.cost.context import bind_run, call_context, run_binding
from codeverse.cost.ledger import (
    CostLedger,
    default_ledger_path,
    existing_ledger_path,
    open_run_ledger,
    record_call,
    set_default_ledger,
)
from codeverse.cost.types import Role, Stage, stage_for_label

log = logging.getLogger(__name__)

#: True while a run is metered call-by-call (:func:`run_ledger`).  Aggregate
#: writers — ``orchestrator.budget.BudgetGuard``, which appends one row per
#: *charge* — must not also write, or every dollar lands in the ledger twice.
#:
#: Context-local first, with a process-wide **count** of the open run ledgers as
#: the fallback for a worker thread that inherited no context.  A plain global
#: boolean cannot survive ``bench.run_bench --parallel N``: two threads entering
#: and leaving their own ledgers interleave, and the second one restores "on"
#: after the first turned it off.
_per_call_var: ContextVar[bool | None] = ContextVar("cv3d_cost_per_call", default=None)
_active_lock = threading.Lock()
_active_ledgers = 0


def per_call_metering() -> bool:
    """True when every model call of the current run is already written to the
    ledger one by one, so a coarser writer should skip its own row."""
    local = _per_call_var.get()
    if local is not None:
        return local
    with _active_lock:
        return _active_ledgers > 0


#: Agent kinds whose model calls go through ``models.get_chat_model`` and are
#: therefore ALREADY one ledger row each (``MeteredChatModel`` wraps that
#: factory).  Every other kind is an opaque subscription CLI: we never see its
#: turns, it hands back one ``AgentResult.usage`` for the whole session, and that
#: session row is the only record of the money.
#:
#: This used to be decided by a thread-local "did anybody write a row while the
#: session ran?" counter, which is wrong in both directions and was reproduced by
#: the verifier (``adversarial.py``): a CLI session during which any in-process
#: tool billed a model (a texture pass, a summariser) looked metered and its whole
#: session — $1.23 in the reproduction — was silently dropped; and an in-process
#: session whose turns ran in another thread looked unmetered and was counted
#: twice.  The backend either meters itself or it does not; that is a property of
#: the backend, not of what happened to run alongside it.
IN_PROCESS_AGENT_KINDS = frozenset({"api-agent"})


def meters_own_calls(agent: Any) -> bool:
    """True when this backend's individual model calls are already on the ledger.

    A backend may state it with a ``meters_own_calls`` attribute; otherwise the
    kind decides (:data:`IN_PROCESS_AGENT_KINDS`)."""
    declared = getattr(agent, "meters_own_calls", None)
    if declared is not None:
        return bool(declared)
    return str(getattr(agent, "kind", "")) in IN_PROCESS_AGENT_KINDS


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

    def supports_vision(self) -> bool:
        return bool(self._inner.supports_vision())

    def __getattr__(self, name: str) -> Any:  # everything else is the real model's
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MeteredChatModel({self._inner!r})"

    # -- the meter --------------------------------------------------------
    def generate(self, request: ChatRequest) -> ChatResponse:
        t0 = time.perf_counter()
        try:
            resp = self._inner.generate(request)
        except BaseException as exc:  # noqa: BLE001 - record the attempt, then re-raise
            self._record(Usage(backend=self.provider, model=self.model), request,
                         outcome=_outcome(exc), ms=int((time.perf_counter() - t0) * 1000))
            raise
        self._record(resp.usage, request, outcome="ok",
                     ms=int((time.perf_counter() - t0) * 1000))
        return resp

    def _record(self, usage: Usage, request: ChatRequest, *, outcome: str, ms: int) -> None:
        try:
            record_call(usage, label=request.label, backend=usage.backend or self.provider,
                        model=usage.model or self.model, outcome=outcome,
                        latency_ms=usage.latency_ms or ms)
        except Exception as e:  # pragma: no cover - accounting must never break a call
            log.debug("cost: could not record %s: %s", request.label, e)


def _outcome(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    return "error"


class MeteredAgent:
    """CodingAgent proxy: attribution for the session + a turn-cap backstop.

    ``tracks.generation.agent_max_turns`` is where the cap is *decided* (it reads
    ``Settings.limits.agent_max_turns`` and handles the wrap-up continuation).
    There is no default cap — a 28-turn one lost its A/B, ``docs/COST.md`` §17 —
    so this is only a backstop for a caller that names one: a bench script, a
    test, ``$CV3D_AGENT_MAX_TURNS``.  It only ever *lowers* ``job.max_turns``, so
    it composes with whatever the caller set."""

    def __init__(self, inner: Any, *, max_turns: int | None = None):
        self._inner = inner
        self.max_turns = max_turns

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
        job = self._capped(job)
        stage = stage_for_label(job.kind or job.label)
        t0 = time.perf_counter()
        with call_context(round=job.round, stage=stage, role=Role.GENERATOR, label=job.label):
            result = self._inner.run(job)
        if not meters_own_calls(self._inner):  # an opaque CLI: its session row is the only record
            self._record_session(job, result, stage, int((time.perf_counter() - t0) * 1000))
        return result

    def _capped(self, job: AgentJob) -> AgentJob:
        cap = self.max_turns if self.max_turns is not None else _settings_turn_cap()
        if cap and 0 < cap < job.max_turns:
            return job.model_copy(update={"max_turns": cap})
        return job

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


def _settings_turn_cap() -> int:
    try:
        from codeverse.config import get_settings

        return int(get_settings().limits.agent_max_turns)
    except Exception:  # pragma: no cover - settings must never break a run
        return 0


# --------------------------------------------------------------------------- factories
def metered_chat_model(model: Any) -> Any:
    """Wrap ``model`` unless it is already metered or metering is off."""
    if model is None or isinstance(model, MeteredChatModel) or not metering_enabled():
        return model
    return MeteredChatModel(model)


def metered_agent(agent: Any) -> Any:
    if agent is None or isinstance(agent, MeteredAgent) or not metering_enabled():
        return agent
    return MeteredAgent(agent)


def metering_enabled() -> bool:
    """``Settings.cost_ledger`` (env ``CV3D_COST_LEDGER=off`` also disables it)."""
    try:
        from codeverse.config import get_settings

        return bool(get_settings().cost_ledger)
    except Exception:  # pragma: no cover
        return True


# --------------------------------------------------------------------------- agent seam
_install_lock = threading.Lock()
_installed = False


def install_agent_metering() -> bool:
    """Make ``get_coding_agent`` hand out metered agents (idempotent).

    The agent package builds its backends through one factory, so wrapping that
    factory is the whole integration; when ``agents/registry.py`` starts calling
    :func:`metered_agent` itself this seam becomes a no-op."""
    global _installed
    with _install_lock:
        if _installed:
            return False
        try:
            import codeverse.agents as agents_pkg
            from codeverse.agents import registry as agents_registry
        except Exception as e:  # pragma: no cover - agents are optional for cost-only use
            log.debug("cost: agent metering unavailable: %s", e)
            return False
        inner = agents_registry.get_coding_agent
        if getattr(inner, "_cv3d_metered", False):
            _installed = True
            return False

        def get_coding_agent(agent_id: str) -> Any:
            return metered_agent(inner(agent_id))

        get_coding_agent._cv3d_metered = True  # type: ignore[attr-defined]
        get_coding_agent.__doc__ = inner.__doc__
        agents_registry.get_coding_agent = get_coding_agent  # type: ignore[assignment]
        agents_pkg.get_coding_agent = get_coding_agent  # type: ignore[attr-defined]
        _installed = True
        return True


# --------------------------------------------------------------------------- run activation
@contextmanager
def run_ledger(workspace: str | Path, *, run: str = "", create: bool = True) -> Iterator[CostLedger | None]:
    """Meter one run into ``<workspace>/telemetry/cost.jsonl``.

    Binds the run name, points :func:`~codeverse.cost.ledger.record_call` at the
    run's ledger and installs the agent seam; restores the previous default on
    the way out so a second run in the same process is not mixed in.

    ``create=False`` is for work done *after* a run finished (``3dcv judge``,
    a post-hoc texture pass): it appends only when the run already keeps a
    ledger, because a ledger holding nothing but the re-judge would be read as
    the whole run's cost and hide everything the run really spent.  Without one
    the rows go to the per-process log instead.

    **Nests and parallelises.**  The previous ledger and run binding are restored
    on the way out rather than cleared, so a bench cell that opens a ledger for
    the cell and then a second one for the harness run inside it keeps both; and
    both are context-local first (see :func:`codeverse.cost.context.bind_run`), so
    ``bench.run_bench`` can run N prompts in N threads without their rows mixing."""
    if not metering_enabled():
        yield None
        return
    global _active_ledgers
    ws = Path(workspace)
    prev_run = run_binding().run
    prev_path = default_ledger_path()
    prev_metering = _per_call_var.get()
    if not create and existing_ledger_path(ws) is None:
        bind_run(run or ws.name)
        try:
            yield None
        finally:
            bind_run(prev_run)
        return
    ledger = open_run_ledger(ws)
    set_default_ledger(ledger.path)
    bind_run(run or ws.name)
    install_agent_metering()
    _per_call_var.set(True)
    with _active_lock:
        _active_ledgers += 1
    try:
        yield ledger
    finally:
        _per_call_var.set(prev_metering)
        with _active_lock:
            _active_ledgers -= 1
        set_default_ledger(prev_path)
        bind_run(prev_run)


__all__ = ["MeteredAgent", "MeteredChatModel", "install_agent_metering", "metered_agent",
           "metered_chat_model", "metering_enabled", "per_call_metering", "run_ledger"]
