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
