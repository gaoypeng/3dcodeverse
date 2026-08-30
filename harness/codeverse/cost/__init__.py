"""Cost accounting: a typed ledger of every model call, a price-provenance layer on
top of ``models/pricing.py``, an audit over recorded runs, and a pre-send cost guard.

This package re-exports only the names other packages actually import through it.
Everything else lives in — and is imported from — its own module: ``cost.report``
(console/markdown), ``cost.profiles`` (get_profile/PROFILES), ``cost.routing``
(ROUTES/Route), ``cost.instrument`` (metered_chat_model/MeteredAgent),
``cost.types`` (CallCost/CostBucket/Summary), ``cost.reconstruct`` (RunLedger).
Keeping the shim narrow also keeps report/profiles/routing out of the eager import
graph of ``import codeverse.cost``.

Nothing here imports tracks/ or orchestrator/, so any layer may use it.
"""

from __future__ import annotations

from codeverse.cost.audit import audit_runs
from codeverse.cost.context import call_context
from codeverse.cost.guard import estimate_call, text_tokens
from codeverse.cost.instrument import run_ledger
from codeverse.cost.ledger import CostLedger, load_ledger, open_run_ledger, record_call, summarise
from codeverse.cost.reconstruct import find_runs, reconstruct
from codeverse.cost.report import markdown
from codeverse.cost.routing import default_route, pro_break_even
from codeverse.cost.types import Role, Stage

__all__ = [
    "CostLedger", "Role", "Stage", "audit_runs", "call_context", "default_route",
    "estimate_call", "find_runs", "load_ledger", "markdown", "open_run_ledger",
    "pro_break_even", "reconstruct", "record_call", "run_ledger",
    "summarise", "text_tokens",
]
