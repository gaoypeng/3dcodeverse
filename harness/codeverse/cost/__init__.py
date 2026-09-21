"""Cost accounting: a typed ledger of every model call, a price-provenance layer on
top of ``models/pricing.py``, and a pre-send cost guard.  (The audit of FINISHED runs and
its report are ``codeverse.addons.costreport``.)

This package re-exports only the names other packages actually import through it.
Everything else lives in — and is imported from — its own module: ``cost.profiles`` (get_profile/PROFILES), ``cost.routing``
(ROUTES/Route), ``cost.instrument`` (metered_chat_model/MeteredAgent),
``cost.types`` (CallCost/CostBucket/Summary), ``cost.reconstruct`` (RunLedger).
Keeping the shim narrow also keeps profiles/routing out of the eager import
graph of ``import codeverse.cost``.

Nothing here imports tracks/ or orchestrator/, so any layer may use it.
"""

from __future__ import annotations

from codeverse.cost.context import call_context
from codeverse.cost.guard import estimate_call
from codeverse.cost.instrument import run_ledger
from codeverse.cost.ledger import CostLedger, load_ledger, record_call, summarise
from codeverse.cost.reconstruct import find_runs, reconstruct
from codeverse.cost.types import Role, Stage

__all__ = [
    "CostLedger", "Role", "Stage", "call_context", "estimate_call",
    "find_runs", "load_ledger", "reconstruct", "record_call", "run_ledger", "summarise",
]
