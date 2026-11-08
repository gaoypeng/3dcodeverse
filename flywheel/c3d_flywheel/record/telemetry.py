"""The cost summary of one run, from its ledger (``record.telemetry`` when the record lacks one)."""

from __future__ import annotations

import logging

from codeverse3d.workspace import Workspace

from c3d_flywheel.cost.ledger import load_ledger, summarise
from c3d_flywheel.cost.types import STAGE_ORDER, CallCost
from c3d_flywheel.legacy import CostSummary, RunRecord, RunTelemetry, StageCost
from c3d_flywheel.record.record import effective_score

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- the ledger (c3d_flywheel.cost)
def ledger_rows(ws: Workspace) -> list[CallCost]:
    """The run's priced per-call rows (none without a ledger)."""
    try:
        return load_ledger(ws.root)
    except Exception as e:  # noqa: BLE001 - accounting must never fail a run
        log.warning("cost ledger unreadable for %s: %s", ws.root, e)
        return []


def cost_summary(record: RunRecord, rows: list[CallCost]) -> CostSummary:
    """The ledger rows as the run layout shows them: total, per stage / role / model / round, and minutes."""
    summ = summarise(rows, dimensions=("stage", "role", "model"))
    rank = {s: i for i, s in enumerate(STAGE_ORDER)}
    by_stage = [StageCost(stage=b.key, calls=b.n_calls, input_tokens=b.input_tokens, output_tokens=b.output_tokens,
                          cached_tokens=b.cached_tokens, thoughts_tokens=b.thoughts_tokens,
                          cost_usd=round(b.cost_usd, 6), seconds=round(b.latency_ms / 1000.0, 1))
                for b in sorted(summ.dimension("stage").values(), key=lambda b: (rank.get(b.key, len(rank)), b.key))]
    minutes = record.minutes
    return CostSummary(
        total_usd=round(summ.total.cost_usd, 6),
        minutes=round(minutes, 2),
        max_minutes=record.spec.budget.max_minutes,
        n_calls=summ.total.n_calls,
        tokens=record.total_usage,
        by_stage=by_stage,
        by_role={k: round(b.cost_usd, 6) for k, b in summ.dimension("role").items()},
        by_model={k: round(b.cost_usd, 6) for k, b in summ.dimension("model").items()},
        by_round=[{"index": r.index, "kind": r.kind, "cost_usd": round(r.usage.cost_usd, 6),
                   "minutes": round(r.minutes, 2), "score": effective_score(r)} for r in record.rounds],
    )


def build_telemetry(ws: Workspace, record: RunRecord) -> RunTelemetry:
    """The cost summary of one run, computed from its ledger (no settings snapshot: that is only ever read)."""
    return RunTelemetry(cost=cost_summary(record, ledger_rows(ws)))
