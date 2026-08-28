"""Did the *provider* fail, or did the *model* fail?

A capacity storm (503/529), a read timeout, or an exhausted key pool says nothing
about the model under test: the cell was never really attempted, so it must be
DROPPED from the comparison rather than scored 0.

Getting this wrong is not a rounding error.  ``compare_backends`` has two failure
paths — a one-shot arm that produced no code, and an exception escaping a harness
arm — and until 2026-08-24 they recorded *the same outage* as ``score=0.0``
(counted in the mean) for the one-shot arm and ``score=None`` (dropped) for the
harness arm.  During the multi-hour gemini-3.7-flash outage of that morning, six
one-shot cells took hard zeros while the harness cells hit by the identical 503
were quietly excluded, biasing every harness-vs-one-shot mean in the harness's
favour.  Both paths now classify through here.

A model that answers with prose, or with code that will not parse, is NOT an infra
failure — that is a real capability result and keeps its zero.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

log = logging.getLogger(__name__)

#: substrings that identify a provider-side outage in a *stringified* error.  Kept
#: narrow on purpose: anything matched here is excluded from the comparison, so a
#: loose pattern would hide genuine model failures.
INFRA_MARKERS: tuple[str, ...] = (
    "currently experiencing high demand",
    "is overloaded",
    "service unavailable",
    "internal server error",
    "request timed out",
    "read operation timed out",
    "deadline exceeded",
    "connection reset",
    "connection aborted",
    # the provider adapters' own wording for a socket/TLS failure with no HTTP status:
    # ModelError("Gemini transport error: ..."), ("Anthropic connection error: ...") and
    # ("OpenAI connection error: ...") matched nothing here, so BOTH arms scored them 0.
    "transport error",
    "connection error",
    "remote end closed connection",
    "keypoolexhausted",
    "resource has been exhausted",
    "quota exceeded",
    "capacity storm",
    # headless Chrome could not get a WebGL context — the shared GPU was saturated by
    # another job (compare_v4_calm / compare_art_v2, 2026-08-25: six cells, all on a
    # box whose GPU 0 sat at 100 % for someone else).  A render that never started says
    # nothing about the code it would have rendered.
    "error creating webgl context",
)

#: HTTP statuses that mean "the provider could not serve this", not "the model was bad".
#: 408 is the adapters' own code for a request timeout (gemini.py:117, openai.py:40,
#: anthropic.py:43) — the exception path must agree with the "request timed out" marker
#: the string path already had.
INFRA_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504, 529})


def _has_marker(text: str) -> bool:
    return any(marker in text.lower() for marker in INFRA_MARKERS)


def _looks_infra(exc: BaseException) -> bool:
    """The outage rules, applied to ONE exception.  Structured first — a ``ModelError`` /
    ``KeyPoolExhausted`` carries its status, and trusting that beats guessing from prose —
    with the string markers as the fallback.  ONE copy: this ran as two hand-kept copies
    (head vs each wrapped cause) until 2026-08-28, which is how a rule reaches one path
    and not the other."""
    if type(exc).__name__ == "KeyPoolExhausted" or isinstance(exc, TimeoutError):
        return True
    status = getattr(exc, "status", None)
    if isinstance(status, int) and status in INFRA_STATUSES:
        return True
    return _has_marker(f"{type(exc).__name__}: {exc}")


def _chain(err: BaseException, limit: int = 32) -> Iterator[BaseException]:
    """``err`` and every exception it was raised from, each yielded once.

    A wrapped cause is common (``raise ModelError(...) from urllib.error.HTTPError``), and
    retry.py's ``raise err from exc`` can close the chain into a CYCLE — classify() returns
    an already-classified ModelError unchanged, so ``err is exc`` and ``err.__cause__ is
    err``.  compare_art_v3 (2026-08-27): walking that chain recursively hit RecursionError
    inside run_cell's except handler, the matrix loop died and 11 finished cells went
    unrecorded.  Hence iterative, seen-set, bounded.
    """
    seen: set[int] = set()
    exc: BaseException | None = err
    while exc is not None and id(exc) not in seen and len(seen) < limit:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__


def is_infra_failure(err: object) -> bool:
    """True when ``err`` (an exception or an error string) is a provider outage.

    TOTAL by contract: four bench drivers call this from inside an ``except`` handler,
    where a raise escapes the handler itself and takes the driver's whole result loop
    with it (compare_art_v3, 2026-08-27).  So anything the walk raises — a pathological
    ``__str__``, a property behind ``.status`` — is logged and answered False.
    """
    if err is None:
        return False
    try:
        if isinstance(err, BaseException):
            return any(_looks_infra(exc) for exc in _chain(err))
        return _has_marker(str(err))
    except Exception:  # noqa: BLE001 — a classifier bug must not cost the caller its cell
        log.exception("is_infra_failure could not classify a %s; recording it as a real failure",
                      type(err).__name__)
        return False


#: a run that hit its wall-clock / dollar ceiling before producing anything.  Distinct
#: from an outage: we DID ask the model, it just never delivered inside the budget.
BUDGET_MARKERS: tuple[str, ...] = (
    "exceeds max_minutes",
    "exceeds max_usd",
    "budget exhausted",
    "wall-clock ceiling",
)


def is_budget_exhaustion(err: object, *, rounds: int = 0) -> bool:
    """True when the cell ran out of time/money with no artifact to show.

    Scoring these 0 is wrong for the same reason an outage is — no artifact was ever
    judged — but they are NOT excused the way an outage is: the arm still failed to
    deliver, so ``build_ok`` counts the miss and the report gives them their own
    column.  (Observed 2026-08-24: a harness cell spent 50 min losing to a 503 storm,
    produced $0.03 of tokens and zero rounds, and was recorded as a hard 0.0.)
    """
    if rounds > 0:
        return False
    text = str(err or "").lower()
    return any(marker in text for marker in BUDGET_MARKERS)
