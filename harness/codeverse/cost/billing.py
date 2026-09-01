"""Which backends actually take dollars out of an account.

``Usage.cost_usd`` answers "what would these tokens cost at list price?".  That is the
right number for a report, a $/complexity-point, or a flywheel record: it is comparable
across backends and it does not depend on who is paying.  It is the WRONG number for
"what did this run bill", because a backend on a local subscription bills no dollars at
all — measured 2026-08-25 on ``tsr_scn_temple_night`` (``codex:gpt-5.6-sol``): two
Blender hero sessions priced at OpenAI list rates read as $7.712 over a bill of exactly
$0.00 (docs/COST.md §25).

So the split is: the LEDGER and the reports price everything, and :func:`bills_usd`
shapes the BILLED figure — ``BudgetGuard.billed_usd`` (``record.json`` / the report's
``spent_usd`` beside ``notional_usd``) and the resume reconcile in
``tracks/lifecycle.py``.  Nothing enforces a dollar ceiling any more (removed in
fbf89a5); the only ceiling is ``max_minutes``, which ``BudgetGuard.timeout_s`` also
clips individual sessions against.
"""

from __future__ import annotations

#: Backends that run on a flat-rate local subscription on this machine, so their
#: ``cost_usd`` is notional (see ``CLAUDE.md`` "Environment": codex / claude / agy run on
#: local subscriptions).  ``gemini-cli`` is deliberately NOT here: it authenticates with
#: an API key, so its tokens draw on a real per-token quota even when that quota is free.
SUBSCRIPTION_BACKENDS = frozenset({"codex", "claude-code", "agy", "antigravity"})


def bills_usd(backend: str | None) -> bool:
    """True when this backend's ``cost_usd`` is money someone is actually charged.

    Unknown backends bill: a new API provider that nobody remembered to classify must be
    reported as spend, not exempted.
    """
    return (backend or "").strip().lower() not in SUBSCRIPTION_BACKENDS
