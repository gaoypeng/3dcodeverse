"""Cost accounting: a typed ledger of every model call, and a price-provenance layer on
top of ``models/pricing.py``.  (The audit of FINISHED runs and
its report are ``c3d_flywheel.costreport``.)

This package re-exports only the names other packages actually import through it.
Everything else lives in — and is imported from — its own module: ``cost.instrument``
(metered_chat_model/MeteredAgent), ``cost.types`` (CallCost/CostBucket/Summary), ``cost.tally`` (the step clock).
Reading the ledgers of finished runs back is ``c3d_flywheel.costreport``.

Nothing here imports tracks/ or orchestrator/, so any layer may use it.
"""

from __future__ import annotations

from c3d_flywheel.cost.context import call_context
from c3d_flywheel.cost.instrument import run_ledger
from c3d_flywheel.cost.ledger import load_ledger, record_call
from c3d_flywheel.cost.types import Role, Stage

__all__ = ["Role", "Stage", "call_context", "load_ledger", "record_call", "run_ledger"]
