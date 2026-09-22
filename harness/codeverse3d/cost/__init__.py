"""Cost accounting: a typed ledger of every model call, a price-provenance layer on
top of ``models/pricing.py``, and a pre-send cost guard.  (The audit of FINISHED runs and
its report are ``codeverse3d.addons.costreport``.)

This package re-exports only the names other packages actually import through it.
Everything else lives in — and is imported from — its own module: ``cost.profiles``
(get_profile/PROFILES), ``cost.instrument`` (metered_chat_model/MeteredAgent),
``cost.types`` (CallCost/CostBucket/Summary), ``cost.reconstruct`` (RunLedger).
Keeping the shim narrow also keeps profiles out of the eager import graph of
``import codeverse3d.cost``.

Nothing here imports tracks/ or orchestrator/, so any layer may use it.
"""

from __future__ import annotations

from codeverse3d.cost.context import call_context
from codeverse3d.cost.guard import estimate_call
from codeverse3d.cost.instrument import run_ledger
from codeverse3d.cost.ledger import CostLedger, load_ledger, record_call, summarise
from codeverse3d.cost.reconstruct import find_runs, reconstruct
from codeverse3d.cost.types import Role, Stage

__all__ = [
    "CostLedger", "Role", "Stage", "call_context", "estimate_call",
    "find_runs", "load_ledger", "reconstruct", "record_call", "run_ledger", "summarise",
]
