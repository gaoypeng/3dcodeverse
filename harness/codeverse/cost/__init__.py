"""Cost accounting: a typed ledger of every model call, a price-provenance layer
on top of ``models/pricing.py``, an audit over recorded runs, and the two cheap
helpers that keep the bill down (cache-friendly prompt ordering, a pre-send cost
guard).

Public API::

    from codeverse.cost import record_call, load_ledger, summarise
    from codeverse.cost import audit_runs, markdown          # the audit + its report
    from codeverse.cost import estimate_call, CostGuard      # decide before you send
    from codeverse.cost import Block, order_blocks           # cache-friendly prompts
    from codeverse.cost import default_route, pro_break_even # model routing

Nothing here imports tracks/ or orchestrator/, so any layer may use it.
"""

from __future__ import annotations

from codeverse.cost.audit import Audit, WasteItem, audit_dir, audit_runs
from codeverse.cost.caching import (
    Block,
    PrefixReport,
    cache_efficiency,
    order_blocks,
    prefix_report,
    prefix_signature,
    render_blocks,
)
from codeverse.cost.guard import CostEstimate, CostGuard, cheapest_affordable, estimate_call, text_tokens
from codeverse.cost.ledger import (
    CostLedger,
    ledger_path,
    load_ledger,
    price_call,
    record_call,
    set_default_ledger,
    summarise,
)
from codeverse.cost.reconstruct import RunLedger, find_runs, reconstruct, reconstruct_run
from codeverse.cost.report import console, markdown
from codeverse.cost.routing import ROUTES, Route, default_route, pro_break_even, samples_for_precision
from codeverse.cost.types import CallCost, CostBucket, Role, Stage, Summary

__all__ = [
    "ROUTES", "Audit", "Block", "CallCost", "CostBucket", "CostEstimate", "CostGuard", "CostLedger",
    "PrefixReport", "Role", "Route", "RunLedger", "Stage", "Summary", "WasteItem", "audit_dir",
    "audit_runs", "cache_efficiency", "cheapest_affordable", "console", "default_route",
    "estimate_call", "find_runs", "ledger_path", "load_ledger", "markdown", "order_blocks",
    "prefix_report", "prefix_signature", "price_call", "pro_break_even", "reconstruct",
    "reconstruct_run", "record_call", "render_blocks", "samples_for_precision",
    "set_default_ledger", "summarise", "text_tokens",
]
