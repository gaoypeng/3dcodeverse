"""Run telemetry: the resolved settings snapshot + the summary of the money ledger.

Beside the ledger itself, ``telemetry/cost.jsonl`` (one priced row per model call, written
while the run happens — ``codeverse3d.cost``), two files under ``<run>/telemetry/`` (see
docs/RUN_LAYOUT.md):

* ``settings.json`` — how the run was configured: model id per role, thinking
  level / temperature / judge samples, rounds + budget, rubric and its content
  hash, prompt & cookbook hashes, tool versions, harness git sha, key-pool size,
  price-table hash, render + limit settings.
* ``cost.json`` — the run-layout summary of the ledger rows: totals, per stage, per
  role, per model, per round, the run's minutes against ``max_minutes``.

The same content is mirrored into ``record.telemetry`` so a consumer that only
has ``record.json`` sees it too.  Nothing here is required for a run to
succeed: every builder degrades to "what could be read".
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from typing import Any

from codeverse3d.contracts.common import TRACK_INFO
from codeverse3d.contracts.run import (
    CostSummary,
    RoleSettings,
    RunRecord,
    RunTelemetry,
    SettingsSnapshot,
    StageCost,
)
from codeverse3d.cost.ledger import TELEMETRY_LEDGER, load_ledger, summarise
from codeverse3d.cost.types import CallCost, Stage
from codeverse3d.proc import read_json_or_none, write_json_atomic
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- settings snapshot
def price_table_version() -> str:
    """Content hash of the USD price table — the run was costed with THIS table."""
    try:
        from codeverse3d.models.pricing import PRICES

        payload = json.dumps(
            {f"{p}/{m}": [v.input, v.output, v.cached, v.cache_write, v.approximate] for (p, m), v in sorted(PRICES.items())},
            sort_keys=True,
        )
    except Exception as e:  # pragma: no cover - defensive
        log.debug("price table unavailable: %s", e)
        return ""
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def backend_kind(model_id: str) -> str:
    """``api-agent:gemini:gemini-3.7-flash`` → ``api-agent``; ``gemini:x`` → ``gemini``."""
    return model_id.split(":", 1)[0] if ":" in model_id else model_id


def _sig_default(fn: Any, name: str) -> Any:
    try:
        param = inspect.signature(fn).parameters[name]
    except (TypeError, ValueError, KeyError):  # pragma: no cover - defensive
        return None
    return None if param.default is inspect.Parameter.empty else param.default


def _judge_defaults() -> dict[str, Any]:
    try:
        from codeverse3d.judges.vlm_judge import VlmJudge
    except Exception:  # pragma: no cover - sub-package missing
        return {}
    init = VlmJudge.__init__
    return {"temperature": _sig_default(init, "temperature"), "thinking": _sig_default(init, "thinking"),
            "n_samples": _sig_default(init, "n_samples")}


def _planner_defaults(record: RunRecord) -> dict[str, Any]:
    """The temperature THIS track plans at (``planner.plan_temperature``: graphics 0.5, the rest 0.4)."""
    from codeverse3d.tracks.planner import plan_temperature

    return {"temperature": plan_temperature(record.spec.track)}


def _generator_defaults(model_id: str) -> dict[str, Any]:
    """HISTORICAL shim, read-only: CLI agents own their sampling knobs and only the
    in-process ``api-agent`` (deleted 2026-08-28) exposed ours.  The constants reproduce
    its shipped defaults so a pre-deletion record still reports what it ran with; they
    configure nothing."""
    if backend_kind(model_id) != "api-agent":
        return {}
    return {"temperature": 0.3, "thinking": "low"}


def _rubric_name(record: RunRecord) -> str:
    name = str(record.extra.get("rubric") or "")
    if name:
        return name
    for r in reversed(record.rounds):
        if r.judgment is not None and r.judgment.rubric:
            return r.judgment.rubric
    info = TRACK_INFO.get(record.spec.track)
    return info.rubric if info else ""


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
    b = record.spec.backends
    plan_d, judge_d = _planner_defaults(record), _judge_defaults()
    spec: list[tuple[str, str, dict[str, Any]]] = [
        ("planner", b.planner, plan_d),
        ("generator", b.generator, _generator_defaults(b.generator)),
        ("judge", b.judge, judge_d),
        ("captioner", b.captioner, {}),
    ]
    roles: list[RoleSettings] = []
    for role, model_id, defaults in spec:
        temperature = defaults.get("temperature")
        roles.append(RoleSettings(
            role=role, model=model_id, backend=backend_kind(model_id),
            thinking=str(defaults.get("thinking") or ""),
            temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
            n_samples=defaults.get("n_samples") if role == "judge" else None,
            source="default" if defaults else "spec",
        ))
    return roles


def settings_snapshot(record: RunRecord) -> SettingsSnapshot:
    """The resolved 'how was this run configured' block (no secrets, ever)."""
    spec = record.spec
    rubric = _rubric_name(record)
    render: dict[str, Any] = {}
    limits: dict[str, Any] = {}
    keys = 0
    try:
        from codeverse3d.config import get_settings

        s = get_settings()
        render = s.render.model_dump(mode="json")
        limits = s.limits.model_dump(mode="json")
        keys = len(s.gemini_api_keys)
    except Exception as e:  # pragma: no cover - settings should always load
        log.debug("settings unavailable: %s", e)
    env = dict(record.environment)
    return SettingsSnapshot(
        roles=_role_settings(record),
        budget=spec.budget.model_dump(mode="json"),
        candidates=spec.options.candidates,
        texture=bool(spec.options.texture),
        seed=spec.seed,
        rubric=rubric,
        rubric_hash=rubric_hash(rubric),
        prompt_hashes=dict(record.prompt_hashes),
        tool_versions={k: v for k, v in env.items() if k in _TOOL_KEYS},
        harness_version=env.get("codeverse3d", ""),
        harness_git_sha=env.get("harness_git_sha", ""),
        key_pool_size=keys,
        price_table_version=price_table_version(),
        render=render,
        limits=limits,
    )


_TOOL_KEYS = frozenset({"python", "platform", "host", "blender", "blender_path", "node", "three",
                        "chrome", "puppeteer", "codeverse3d", "harness_git_sha"})


# --------------------------------------------------------------------------- the ledger (codeverse3d.cost)
def stage_order() -> tuple[str, ...]:
    return tuple(s.value for s in Stage)


def ledger_rows(ws: Workspace) -> list[CallCost]:
    """The run's priced per-call rows, ``telemetry/cost.jsonl`` (none when it has no ledger)."""
    try:
        return load_ledger(ws.root)
    except Exception as e:  # noqa: BLE001 - accounting must never fail a run
        log.warning("cost ledger unreadable for %s: %s", ws.root, e)
        return []


def cost_summary(record: RunRecord, rows: list[CallCost]) -> CostSummary:
    """The ledger rows as the run layout shows them: total, per stage / role / model (the
    ledger's own ``summarise`` — money by stage is computed there and nowhere else), per round,
    and the run's minutes against ``max_minutes``."""
    summ = summarise(rows, dimensions=("stage", "role", "model"))
    rank = {s: i for i, s in enumerate(stage_order())}
    by_stage = [StageCost(stage=b.key, calls=b.n_calls, input_tokens=b.input_tokens, output_tokens=b.output_tokens,
                          cached_tokens=b.cached_tokens, thoughts_tokens=b.thoughts_tokens,
                          cost_usd=round(b.cost_usd, 6), seconds=round(b.latency_ms / 1000.0, 1))
                for b in sorted(summ.dimension("stage").values(), key=lambda b: (rank.get(b.key, len(rank)), b.key))]
    minutes = record.minutes
    return CostSummary(
        total_usd=round(summ.total.cost_usd, 6),
        minutes=None if minutes is None else round(minutes, 2),
        max_minutes=record.spec.budget.max_minutes,
        n_calls=summ.total.n_calls,
        tokens=record.total_usage,
        by_stage=by_stage,
        by_role={k: round(b.cost_usd, 6) for k, b in summ.dimension("role").items()},
        by_model={k: round(b.cost_usd, 6) for k, b in summ.dimension("model").items()},
        by_round=[{"index": r.index, "kind": r.kind, "cost_usd": round(r.usage.cost_usd, 6),
                   "minutes": round(r.minutes, 2), "score": r.score} for r in record.rounds],
    )


# --------------------------------------------------------------------------- io
def build_telemetry(ws: Workspace, record: RunRecord, *, write: bool = True) -> RunTelemetry:
    """Compute (and by default persist) ``telemetry/`` for one run."""
    rows = ledger_rows(ws)
    tele = RunTelemetry(
        settings=settings_snapshot(record),
        cost=cost_summary(record, rows),
        environment=dict(record.environment),
        files={"settings": "telemetry/settings.json",
               "cost": "telemetry/cost.json",
               "ledger": TELEMETRY_LEDGER,
               "events": "telemetry/events.jsonl",
               "run_state": "telemetry/run_state.json",
               "stages": "telemetry/stages",
               "trajectories": "telemetry/trajectories"},
    )
    if write:
        ws.ensure_layout()
        write_json_atomic(ws.settings_path, tele.settings.model_dump(mode="json") if tele.settings else {})
        write_json_atomic(ws.cost_path, tele.cost.model_dump(mode="json") if tele.cost else {})
    return tele


def load_telemetry(ws: Workspace, record: RunRecord | None = None) -> RunTelemetry | None:
    """``record.telemetry`` when present, else the files under ``telemetry/``, else None."""
    if record is not None and record.telemetry is not None:
        return record.telemetry
    cost = read_json_or_none(ws.cost_path)
    settings = read_json_or_none(ws.settings_path)
    if cost is None and settings is None:
        return None
    try:
        return RunTelemetry(
            settings=SettingsSnapshot.model_validate(settings) if settings else None,
            cost=CostSummary.model_validate(cost) if cost else None,
            environment=dict(record.environment) if record is not None else {},
        )
    except Exception as e:  # a hand-edited file must not break status/export
        log.warning("unreadable telemetry in %s: %s", ws.root, e)
        return None
