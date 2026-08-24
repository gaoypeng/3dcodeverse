"""Is a model actually serving right now?

A batch job is the worst place to discover a provider outage.  On 2026-08-24 a
``gemini-3.7-flash`` capacity storm ran for hours; eight bench cells were launched
into it and each spent 56-87 minutes losing to 503s before recording a failure, and
the judge model went down with it.  The provider was unreachable within seconds of
the first call — the cost was entirely in not asking.

``probe`` asks once per key, cheaply and in parallel, with a short timeout and no
retries: the point is a fast verdict, not a successful call.  Callers use it as a
preflight gate before spending real money.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: enough of the pool to tell an outage from one unlucky key, without a long probe
DEFAULT_SAMPLE = 4
#: a healthy model answers a 5-token prompt well inside this
DEFAULT_TIMEOUT_S = 20.0
#: below this share of answering keys, treat the model as down
DEFAULT_MIN_OK = 0.5


@dataclass(frozen=True)
class Health:
    model: str
    n_ok: int
    n_tried: int
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.n_tried > 0 and self.n_ok / self.n_tried >= DEFAULT_MIN_OK

    def __str__(self) -> str:
        head = f"{self.model}: {self.n_ok}/{self.n_tried} keys answering"
        return head if self.ok else f"{head} — {'; '.join(self.reasons[:2]) or 'no reason reported'}"


def probe(model: str = "gemini:gemini-3.7-flash", *, sample: int = DEFAULT_SAMPLE,
          timeout_s: float = DEFAULT_TIMEOUT_S) -> Health:
    """One tiny call per sampled key, in parallel, no retries.  Never raises."""
    from codeverse.contracts.chat import ChatMessage, ChatRequest

    def one(_: int) -> str | None:
        try:
            # built directly, not via get_chat_model: a probe must NOT retry or back off
            m = _bare_model(model, timeout_s)
            m.generate(ChatRequest(messages=[ChatMessage.user("Reply with the single word: pong")],
                                   temperature=0.0, max_output_tokens=256, thinking="off", label="health"))
            return None
        except Exception as e:  # noqa: BLE001 — a probe reports, it does not propagate
            return f"{type(e).__name__}: {str(e)[:100]}"

    n = max(1, sample)
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(one, range(n)))
    reasons = tuple(dict.fromkeys(r for r in out if r))  # de-duplicated, order kept
    return Health(model=model, n_ok=sum(r is None for r in out), n_tried=n, reasons=reasons)


def _bare_model(model_id: str, timeout_s: float):
    """The model with retries switched off — a probe that retries measures nothing."""
    provider, _, name = model_id.partition(":")
    if not name:
        provider, name = "gemini", provider
    if provider == "gemini":
        from codeverse.models.gemini import GeminiModel

        return GeminiModel(name, timeout_s=timeout_s, max_attempts=1)
    from codeverse.models import get_chat_model  # other providers: no bare constructor needed yet

    return get_chat_model(model_id)


# --------------------------------------------------------------------------- siblings
#: how a harness process appears in its own argv.  Matched on ARGUMENTS, never on the
#: whole command line: the repo path itself contains "3dcodeverse", so a substring test
#: counts every shell that merely `cd`s into the tree.
_MODULE = "codeverse.cli.main"
_SCRIPTS = ("compare_backends.py", "run_bench.py")
_ENTRY_POINTS = ("3dcv", "3dcodeverse")


def _is_harness_argv(args: list[str]) -> bool:
    if not args:
        return False
    from pathlib import PurePath

    if PurePath(args[0]).name in _ENTRY_POINTS:
        return True
    for i, a in enumerate(args):
        if a == "-m" and i + 1 < len(args) and args[i + 1] == _MODULE:
            return True
        if PurePath(a).name in _SCRIPTS:
            return True
    return False


def sibling_processes() -> int:
    """How many OTHER harness processes are running on this machine.

    ``shared_pool`` keeps one KeyPool per PROCESS, so each sibling believes it owns the
    whole key quota and gets its own ``max_in_flight``.  N siblings multiply the real
    concurrency by N against a quota that is shared: on 2026-08-24 six concurrent
    batteries took gemini-3.7-flash from 25 % success to 0 % (``docs/COST.md`` §23).

    Best-effort — reads /proc and returns 0 when it cannot tell, because a wrong number
    here must never block a run.
    """
    import os
    from pathlib import Path

    me, found = os.getpid(), 0
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue  # exited between listing and reading
        if _is_harness_argv([a for a in raw.decode(errors="replace").split("\0") if a]):
            found += 1
    return found
