"""Is a model actually serving right now?  ``probe`` asks once per key, in parallel, with a
short timeout and no retries; callers use it as a preflight gate before spending real money.
The probe prompt is workload-sized: a tiny prompt can pass while large calls still fail.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import dataclass

from codeverse3d.contracts.common import Backends
from codeverse3d.models.registry import build_chat_model

log = logging.getLogger(__name__)

#: pool keys sampled per probe
DEFAULT_SAMPLE = 6
DEFAULT_TIMEOUT_S = 30.0
#: probe prompt size in tokens, sized like a real planner/generator call
PROBE_TOKENS = 8000
#: below this share of answering calls the model counts as down (a battery needs many calls to land)
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
        return (
            head if self.ok else f"{head} — {'; '.join(self.reasons[:2]) or 'no reason reported'}"
        )


def probe(
    model: str = Backends().planner,
    *,
    sample: int = DEFAULT_SAMPLE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Health:
    """One workload-sized call (``PROBE_TOKENS``) per sampled key, in parallel, no retries.  Never raises."""
    from codeverse3d.contracts.chat import ChatMessage, ChatRequest

    prompt = _probe_prompt()

    def one(_: int) -> str | None:
        try:
            # built directly, not via get_chat_model: a probe must NOT retry or back off
            m = _bare_model(model, timeout_s)
            m.generate(
                ChatRequest(
                    messages=[ChatMessage.user(prompt)],
                    temperature=0.0,
                    max_output_tokens=256,
                    thinking="off",
                    label="health",
                )
            )
            return None
        except Exception as e:  # noqa: BLE001 — a probe reports, it does not propagate
            return f"{type(e).__name__}: {str(e)[:100]}"

    n = max(1, sample)
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(one, range(n)))
    reasons = tuple(dict.fromkeys(r for r in out if r))  # de-duplicated, order kept
    return Health(model=model, n_ok=sum(r is None for r in out), n_tried=n, reasons=reasons)


def _probe_prompt(tokens: int = PROBE_TOKENS) -> str:
    """A workload-sized prompt whose answer is still one word."""
    filler = "The quick brown fox jumps over the lazy dog. " * max(1, tokens // 9)
    return f"{filler}\n\nIgnore the text above. Reply with the single word: pong"


def _bare_model(model_id: str, timeout_s: float):
    """The model with retries off; a bare name is a gemini model."""
    if ":" not in model_id:
        model_id = f"gemini:{model_id}"
    extra = {"storm_attempts": 0} if model_id.startswith("gemini:") else {}
    return build_chat_model(model_id, timeout_s=timeout_s, max_attempts=1, **extra)
