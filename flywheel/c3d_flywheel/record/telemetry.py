"""Run telemetry, stored in ``record.telemetry``: how the run was configured (the settings snapshot)
and a summary of the ledger rows.  Builders degrade to what could be read."""

from __future__ import annotations

import hashlib
import json
import logging

from codeverse3d.config import get_settings
from codeverse3d.contracts.run import (
    CostSummary,
    RoleSettings,
    RunRecord,
    RunTelemetry,
    SettingsSnapshot,
    StageCost,
)
from c3d_flywheel.cost.ledger import load_ledger, summarise
from c3d_flywheel.cost.types import STAGE_ORDER, CallCost
from c3d_flywheel.record.record import effective_score, rubric_of
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- settings snapshot
def price_table_version() -> str:
    """Content hash of the USD price table."""
    from c3d_flywheel.models.pricing import PRICES

    payload = json.dumps(
        {f"{p}/{m}": [v.input, v.output, v.cached, v.cache_write, v.approximate] for (p, m), v in sorted(PRICES.items())},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def backend_kind(model_id: str) -> str:
    """``gemini:x`` → ``gemini``."""
    return model_id.split(":", 1)[0] if ":" in model_id else model_id


def rubric_hash(name: str) -> str:
    if not name:
        return ""
    try:
        from codeverse3d.judges.rubrics import load_rubric

        return load_rubric(name).content_hash()
    except Exception as e:  # unknown rubric / package missing
        log.debug("rubric hash unavailable for %s: %s", name, e)
        return ""


def _role_settings(record: RunRecord) -> list[RoleSettings]:
    """The model per role from the spec; sampling knobs from ``record.extra["sampling"]``, else left empty."""
    b = record.spec.backends
    sampling = record.extra.get("sampling") or {}
    roles: list[RoleSettings] = []
    for role, model_id in (("planner", b.planner), ("generator", b.generator), ("judge", b.judge),
                           ("captioner", b.captioner)):
        knobs = sampling.get(role) or {}
        temperature = knobs.get("temperature")
        roles.append(RoleSettings(
            role=role, model=model_id, backend=backend_kind(model_id), thinking=str(knobs.get("thinking") or ""),
            temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
            n_samples=knobs.get("n_samples") if role == "judge" else None, source="run" if knobs else "spec",
        ))
    return roles


def settings_snapshot(record: RunRecord) -> SettingsSnapshot:
    """The resolved configuration block (no secrets)."""
    spec = record.spec
    rubric = rubric_of(record)
    s = get_settings()
    render = s.render.model_dump(mode="json")
    keys = len(s.gemini_api_keys)
    env = dict(record.environment)
    return SettingsSnapshot(
        roles=_role_settings(record),
        budget=spec.budget.model_dump(mode="json"),
        rubric=rubric,
        rubric_hash=rubric_hash(rubric),
        prompt_hashes=dict(record.prompt_hashes),
        tool_versions={k: v for k, v in env.items() if k in _TOOL_KEYS},
        harness_version=env.get("codeverse3d", ""),
        harness_git_sha=env.get("harness_git_sha", ""),
        key_pool_size=keys,
        price_table_version=price_table_version(),
        render=render,
    )


_TOOL_KEYS = frozenset({"python", "platform", "host", "blender", "blender_path", "node", "three",
                        "chrome", "puppeteer", "codeverse3d", "harness_git_sha"})


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


# --------------------------------------------------------------------------- build
def build_telemetry(ws: Workspace, record: RunRecord) -> RunTelemetry:
    """Compute the settings snapshot and the cost summary of one run (``record.telemetry``)."""
    return RunTelemetry(settings=settings_snapshot(record), cost=cost_summary(record, ledger_rows(ws)))
