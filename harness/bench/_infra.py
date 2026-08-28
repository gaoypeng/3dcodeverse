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
    "deadline expired",  # Gemini 504
    "exceeded its attempt budget",  # models/gemini.py streaming: the provider held the socket past the attempt budget (2026-08-28, pro planner)
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


def is_infra_failure(err: object) -> bool:
    """True when ``err`` (an exception or an error string) is a provider outage.

    Structured first — a ``ModelError``/``KeyPoolExhausted`` carries its status, and
    trusting that beats guessing from prose.  The string markers are the fallback for
    errors that reach the recorder already stringified.
    """
    if err is None:
        return False
    if isinstance(err, BaseException):
        if type(err).__name__ == "KeyPoolExhausted":
            return True
        status = getattr(err, "status", None)
        if isinstance(status, int) and status in INFRA_STATUSES:
            return True
        if isinstance(err, TimeoutError):
            return True
        # a wrapped cause is common: `raise ModelError(...) from urllib.error.HTTPError`.
        # Walk the chain ITERATIVELY with a visited set: retry.py's `raise err from exc`
        # can close the chain into a cycle (compare_art_v3, 2026-08-27: a truncated plan's
        # ModelError → RecursionError inside run_cell's except handler → the matrix loop
        # died and 11 finished cells went unrecorded).
        seen: set[int] = {id(err)}
        cause = err.__cause__ if err.__cause__ is not None else err.__context__
        while cause is not None and id(cause) not in seen and len(seen) < 32:
            seen.add(id(cause))
            if isinstance(cause, BaseException):
                if type(cause).__name__ == "KeyPoolExhausted" or isinstance(cause, TimeoutError):
                    return True
                status = getattr(cause, "status", None)
                if isinstance(status, int) and status in INFRA_STATUSES:
                    return True
                if any(marker in f"{type(cause).__name__}: {cause}".lower() for marker in INFRA_MARKERS):
                    return True
                cause = cause.__cause__ if cause.__cause__ is not None else cause.__context__
            else:
                break
        err = f"{type(err).__name__}: {err}"
    text = str(err).lower()
    return any(marker in text for marker in INFRA_MARKERS)


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
