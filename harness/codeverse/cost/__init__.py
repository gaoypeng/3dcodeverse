"""Cost accounting: a typed ledger of every model call, a price-provenance layer
on top of ``models/pricing.py``, an audit over recorded runs, a pre-send cost
guard, and the prompt-prefix measurement helpers (``Block`` / ``order_blocks`` /
``prefix_report`` — measurement only: reordering the generation prompts for the
cache was measured and reverted, see ``docs/COST.md`` §13).

Public API::

    from codeverse.cost import record_call, load_ledger, summarise
    from codeverse.cost import audit_runs, markdown          # the audit + its report
    from codeverse.cost import estimate_call, CostGuard      # decide before you send
    from codeverse.cost import Block, order_blocks           # cache-friendly prompts
    from codeverse.cost import default_route, pro_break_even # model routing

Nothing here imports tracks/ or orchestrator/, so any layer may use it.
"""

from __future__ import annotations

from codeverse.cost.audit import Audit, WasteItem, audit_runs
from codeverse.cost.caching import (
    Block,
    PrefixReport,
    cache_efficiency,
    order_blocks,
    prefix_report,
    prefix_signature,
    render_blocks,
)
from codeverse.cost.context import CallContext, bind_run, call_context
from codeverse.cost.guard import (
    CostEstimate,
    CostGuard,
    cheapest_affordable,
    estimate_call,
    text_tokens,
)
from codeverse.cost.instrument import (
    MeteredAgent,
    MeteredChatModel,
    metered_agent,
    metered_chat_model,
    per_call_metering,
    run_ledger,
)
from codeverse.cost.ledger import (
    CostLedger,
    ledger_path,
    load_ledger,
    open_run_ledger,
    price_call,
    record_call,
    set_default_ledger,
    summarise,
)
from codeverse.cost.profiles import PROFILES, Profile, get_profile
from codeverse.cost.reconstruct import RunLedger, find_runs, reconstruct, reconstruct_run
from codeverse.cost.report import console, markdown
from codeverse.cost.routing import (
    ROUTES,
    Route,
    default_route,
    pro_break_even,
    samples_for_precision,
)
from codeverse.cost.types import CallCost, CostBucket, Role, Stage, Summary

__all__ = [
    "Audit",
    "Block",
    "CallContext",
    "CallCost",
    "CostBucket",
    "CostEstimate",
    "CostGuard",
    "CostLedger",
    "MeteredAgent",
    "MeteredChatModel",
    "PROFILES",
    "PrefixReport",
    "Profile",
    "ROUTES",
    "Role",
    "Route",
    "RunLedger",
    "Stage",
    "Summary",
    "WasteItem",
    "audit_runs",
    "bind_run",
    "cache_efficiency",
    "call_context",
    "cheapest_affordable",
    "console",
    "default_route",
    "estimate_call",
    "find_runs",
    "get_profile",
    "ledger_path",
    "load_ledger",
    "markdown",
    "metered_agent",
    "metered_chat_model",
    "open_run_ledger",
    "order_blocks",
    "per_call_metering",
    "prefix_report",
    "prefix_signature",
    "price_call",
    "pro_break_even",
    "reconstruct",
    "reconstruct_run",
    "record_call",
    "render_blocks",
    "run_ledger",
    "samples_for_precision",
    "set_default_ledger",
    "summarise",
    "text_tokens",
]
