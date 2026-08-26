"""Which backends actually take dollars out of an account.

``Usage.cost_usd`` answers "what would these tokens cost at list price?".  That is the
right number for a report, a $/complexity-point, or a flywheel record: it is comparable
across backends and it does not depend on who is paying.  It is the WRONG number for a
spend guard, because a backend on a local subscription bills no dollars at all.

Conflating the two degraded real runs.  Measured 2026-08-25 on
``tsr_scn_temple_night`` (``codex:gpt-5.6-sol``, ``--profile quality``): two Blender hero
sessions priced at OpenAI list rates put the run at $7.712 against the profile's $4.40
soft cap in 6.8 minutes, so the asset judge was skipped for both heroes and every later
stage ran degraded — over a bill of exactly $0.00.  The run's own cost ledger agreed it
was $0.00; only the guard disagreed.

So the split is: the LEDGER and the reports keep pricing everything, and the BUDGET
enforces only what is billed.  See ``orchestrator/budget.py`` for the enforcement side
and ``docs/COST.md`` §25.

What a subscription backend is still bounded by: ``max_minutes``, which
``BudgetMeter.timeout_s`` also clips individual sessions against.  Wall clock, not money,
is the scarce resource when the money is flat-rate — a runaway session is still stopped,
just by the ceiling that actually applies to it.
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
    enforced, not exempted.  Getting this wrong in the safe direction costs a degraded
    run; getting it wrong in the other direction spends real money with no ceiling.
    """
    return (backend or "").strip().lower() not in SUBSCRIPTION_BACKENDS
