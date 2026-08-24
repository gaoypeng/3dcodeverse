"""Is a model actually serving right now?

A batch job is the worst place to discover a provider outage.  On 2026-08-24 a
``gemini-3.7-flash`` capacity storm ran for hours; eight bench cells were launched
into it and each spent 56-87 minutes losing to 503s before recording a failure, and
the judge model went down with it.  The provider was unreachable within seconds of
the first call — the cost was entirely in not asking.

``probe`` asks once per key, in parallel, with a short timeout and no retries: the
point is a fast verdict, not a successful call.  Callers use it as a preflight gate
before spending real money.

The probe prompt is deliberately **the size of real work**, not five tokens.  Measured
2026-08-24: during the flash degradation a trivial prompt answered while the 12-24 k
token planner calls beside it were still 503-ing, so a tiny probe passed the preflight
and the battery then lost two cells to the wall at 15 min each.  A probe that does not
resemble the workload does not measure the workload.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: enough of the pool to tell an outage from one unlucky key, without a long probe
DEFAULT_SAMPLE = 6
#: a healthy model answers a workload-sized prompt well inside this
DEFAULT_TIMEOUT_S = 30.0
#: prompt size the probe sends, in tokens.  Sized like a real planner/generator call
#: (docs/COST.md §20 measured the corpus at ~12 k input): a 5-token probe answers when
#: a 12 k one does not, which is exactly the false green light this gate must not give.
#: 4 probes x 8 k tokens is ~$0.003 on flash — irrelevant next to one wasted battery.
PROBE_TOKENS = 8000
#: below this share of answering calls, treat the model as down.
#:
#: NOT 0.5.  A preflight guards a BATTERY, and one cell is dozens of model calls that
#: must all land: at p per call a 20-call cell completes with p**20, so p = 0.5 is not
#: "half healthy", it is zero.  Retries soften it — roughly p >= 0.7 for a coin-flip
#: chance at a cell — so the bar sits above that with margin.  Measured 2026-08-24: the
#: gate passed flash at 2/4 and the battery then lost both its first cells to the retry
#: deadline at ~15 min each.  The asymmetry decides the tie: refusing wrongly costs one
#: 2-minute re-probe, starting wrongly costs hours.  A judgement call, not a measured
#: optimum.
DEFAULT_MIN_OK = 0.75


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

    prompt = _probe_prompt()

    def one(_: int) -> str | None:
        try:
            # built directly, not via get_chat_model: a probe must NOT retry or back off
            m = _bare_model(model, timeout_s)
            m.generate(ChatRequest(messages=[ChatMessage.user(prompt)],
                                   temperature=0.0, max_output_tokens=256, thinking="off", label="health"))
            return None
        except Exception as e:  # noqa: BLE001 — a probe reports, it does not propagate
            return f"{type(e).__name__}: {str(e)[:100]}"

    n = max(1, sample)
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(one, range(n)))
    reasons = tuple(dict.fromkeys(r for r in out if r))  # de-duplicated, order kept
    return Health(model=model, n_ok=sum(r is None for r in out), n_tried=n, reasons=reasons)


def _probe_prompt(tokens: int = PROBE_TOKENS) -> str:
    """A workload-sized prompt whose answer is still one word.

    Filler carries the token weight; the instruction at the end keeps the RESPONSE
    tiny, so the probe costs input tokens (cheap, and the thing under test) rather
    than output tokens.
    """
    filler = "The quick brown fox jumps over the lazy dog. " * max(1, tokens // 9)
    return f"{filler}\n\nIgnore the text above. Reply with the single word: pong"


def _bare_model(model_id: str, timeout_s: float):
    """The model with retries switched off — a probe that retries measures nothing."""
    provider, _, name = model_id.partition(":")
    if not name:
        provider, name = "gemini", provider
    if provider == "gemini":
        from codeverse.models.gemini import GeminiModel

        return GeminiModel(name, timeout_s=timeout_s, max_attempts=1, storm_attempts=0)
    from codeverse.models import get_chat_model  # other providers: no bare constructor needed yet

    return get_chat_model(model_id)


# --------------------------------------------------------------------------- siblings
#: how a harness process appears in its own argv.  Matched on ARGUMENTS, never on the
#: whole command line: the repo path itself contains "3dcodeverse", so a substring test
#: counts every shell that merely `cd`s into the tree.
_MODULE = "codeverse.cli.main"
_SCRIPTS = ("compare_backends.py", "run_bench.py", "ab_plan.py")
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
