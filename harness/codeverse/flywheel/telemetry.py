"""Run telemetry: the resolved settings snapshot + the money ledger.

Three files under ``<run>/telemetry/`` (see docs/RUN_LAYOUT.md):

* ``settings.json`` — how the run was configured: model id per role, thinking
  level / temperature / judge samples, rounds + budget, rubric and its content
  hash, prompt & cookbook hashes, tool versions, harness git sha, key-pool size,
  price-table hash, render + limit settings.
* ``usage.jsonl`` — one priced row per model call.  The rows are
  ``codeverse.cost`` ledger rows (``CallCost``): the live ledger of the run when
  it has one, else ``cost.reconstruct.reconstruct_run`` rebuilds them from the
  trajectories, judge verdicts and priced events.  There is exactly one ledger
  in the harness; this bucket only gives it a stable place in the run directory.
* ``cost.json`` — the run-layout summary of those rows: totals, per stage, per
  role, per model, per round, budget vs spent, wall clock.

The same content is mirrored into ``record.telemetry`` so a consumer that only
has ``record.json`` sees it too.  Nothing here is required for a run to
succeed: every builder degrades to "what could be read".
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

from codeverse.contracts.common import TRACK_INFO
from codeverse.contracts.run import (
    CostSummary,
    RoleSettings,
    RunRecord,
    RunTelemetry,
    SettingsSnapshot,
    StageCost,
)
from codeverse.proc import read_json_or_none, write_json_atomic, write_text_atomic
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- settings snapshot
def price_table_version() -> str:
    """Content hash of the USD price table — the run was costed with THIS table."""
    try:
        from codeverse.models.pricing import PRICES

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
        from codeverse.judges.vlm_judge import VlmJudge
    except Exception:  # pragma: no cover - sub-package missing
        return {}
    init = VlmJudge.__init__
    return {"temperature": _sig_default(init, "temperature"), "thinking": _sig_default(init, "thinking"),
            "n_samples": _sig_default(init, "n_samples")}


def _planner_defaults() -> dict[str, Any]:
    try:
        from codeverse.tracks.planner import plan
    except Exception:  # pragma: no cover
        return {}
    return {"temperature": _sig_default(plan, "temperature")}


def _generator_defaults(model_id: str) -> dict[str, Any]:
    """Only the in-process ``api-agent`` exposes sampling knobs; CLI agents own theirs."""
    if backend_kind(model_id) != "api-agent":
        return {}
    try:
        from codeverse.contracts.agent import ApiAgentOptions
    except Exception:  # pragma: no cover
        return {}
    o = ApiAgentOptions()
    return {"temperature": o.temperature, "thinking": o.thinking}


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
        from codeverse.judges.rubrics import load_rubric

        return load_rubric(name).content_hash()
    except Exception as e:  # unknown rubric / package missing
        log.debug("rubric hash unavailable for %s: %s", name, e)
        return ""


def _role_settings(record: RunRecord) -> list[RoleSettings]:
    b = record.spec.backends
    observed = record.extra.get("sampling") or {}  # forward hook: a track may record real values
    plan_d, judge_d = _planner_defaults(), _judge_defaults()
    spec: list[tuple[str, str, dict[str, Any]]] = [
        ("planner", b.planner, plan_d),
        ("generator", b.generator, _generator_defaults(b.generator)),
        ("judge", b.judge, judge_d),
        ("captioner", b.captioner, {}),
    ]
    roles: list[RoleSettings] = []
    for role, model_id, defaults in spec:
        values = dict(defaults)
        source = "default" if defaults else "spec"
        if isinstance(observed.get(role), dict):
            values.update(observed[role])
            source = "observed"
        temperature = values.get("temperature")
        roles.append(RoleSettings(
            role=role, model=model_id, backend=backend_kind(model_id),
            thinking=str(values.get("thinking") or ""),
            temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
            n_samples=values.get("n_samples") if role == "judge" else None,
            source=source,
        ))
    return roles


def settings_snapshot(record: RunRecord, *, key_pool_size: int | None = None) -> SettingsSnapshot:
    """The resolved 'how was this run configured' block (no secrets, ever)."""
    spec = record.spec
    rubric = _rubric_name(record)
    render: dict[str, Any] = {}
    limits: dict[str, Any] = {}
    keys = key_pool_size
    try:
        from codeverse.config import get_settings

        s = get_settings()
        render = s.render.model_dump(mode="json")
        limits = s.limits.model_dump(mode="json")
        if keys is None:
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
        harness_version=env.get("codeverse", ""),
        harness_git_sha=env.get("harness_git_sha", ""),
        key_pool_size=int(keys or 0),
        price_table_version=price_table_version(),
        render=render,
        limits=limits,
    )


_TOOL_KEYS = frozenset({"python", "platform", "host", "blender", "blender_path", "node", "three",
                        "chrome", "puppeteer", "codeverse", "harness_git_sha"})


# --------------------------------------------------------------------------- the ledger (codeverse.cost)
#: fallback stage order when ``codeverse.cost`` is not importable
_FALLBACK_STAGES = ("plan", "skeleton", "assets", "env", "zones", "assemble", "baseline", "candidate",
                    "repair", "refine", "gates", "render", "judge", "pairwise", "texture", "caption", "other")
#: the live ledger a run may already carry at its root
_FALLBACK_LEDGER_NAME = "cost_ledger.jsonl"


def stage_order() -> tuple[str, ...]:
    try:
        from codeverse.cost.types import Stage

        return tuple(s.value for s in Stage)
    except Exception:  # noqa: BLE001 - the cost package is optional here
        return _FALLBACK_STAGES


def live_ledger_path(ws: Workspace) -> Path:
    """``<run>/cost_ledger.jsonl`` — the ledger written while the run happens, when there is one."""
    try:
        from codeverse.cost.ledger import LEDGER_NAME
    except Exception:  # noqa: BLE001
        LEDGER_NAME = _FALLBACK_LEDGER_NAME  # noqa: N806
    return ws.root / LEDGER_NAME


def ledger_rows(ws: Workspace) -> tuple[list[dict[str, Any]], str]:
    """Priced per-call rows for the run → ``(rows, source)``.

    Source is ``live`` (the run's own ``cost_ledger.jsonl``), ``reconstructed``
    (rebuilt by ``codeverse.cost.reconstruct`` from trajectories / verdicts /
    events) or ``unavailable``.  The cost package owns the pricing and the
    double-counting rules — this bucket never re-implements them."""
    try:
        live = live_ledger_path(ws)
        if live.is_file():
            from codeverse.cost.ledger import load_ledger

            return [r.model_dump(mode="json") for r in load_ledger(live)], "live"
        from codeverse.cost.reconstruct import reconstruct_run

        return [r.model_dump(mode="json") for r in reconstruct_run(ws.root).rows], "reconstructed"
    except Exception as e:  # noqa: BLE001 - accounting must never fail a run
        log.warning("cost ledger unavailable for %s: %s", ws.root, e)
        return [], "unavailable"


def _num(row: dict[str, Any], key: str) -> float:
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


def cost_summary(record: RunRecord, rows: list[dict[str, Any]]) -> CostSummary:
    """Money + tokens: totals, per stage, per role, per model, per round, budget, wall clock."""
    by_stage: dict[str, StageCost] = {}
    by_model: dict[str, float] = {}
    by_role: dict[str, float] = {}
    residual = 0.0
    for row in rows:
        stage = str(row.get("stage") or "other")
        sc = by_stage.setdefault(stage, StageCost(stage=stage))
        sc.calls += int(row.get("n_calls") or 1)
        sc.input_tokens += int(_num(row, "input_tokens"))
        sc.output_tokens += int(_num(row, "output_tokens"))
        sc.cached_tokens += int(_num(row, "cached_tokens"))
        sc.thoughts_tokens += int(_num(row, "thoughts_tokens"))
        sc.cost_usd += _num(row, "cost_usd")
        sc.seconds += _num(row, "latency_ms") / 1000.0
        model = str(row.get("model") or row.get("model_id") or "(unknown)")
        by_model[model] = round(by_model.get(model, 0.0) + _num(row, "cost_usd"), 6)
        role = str(row.get("role") or "other")
        by_role[role] = round(by_role.get(role, 0.0) + _num(row, "cost_usd"), 6)
        if str(row.get("source")) == "residual" or str(row.get("label")) == "unattributed":
            residual += _num(row, "cost_usd")
    for sc in by_stage.values():
        sc.cost_usd = round(sc.cost_usd, 6)
        sc.seconds = round(sc.seconds, 1)
    order = stage_order()
    ordered = [by_stage[s] for s in order if s in by_stage]
    ordered += [sc for s, sc in sorted(by_stage.items()) if s not in order]
    total = float(record.total_usage.cost_usd)
    ledger = round(sum(_num(r, "cost_usd") for r in rows), 6)
    wall = 0.0
    if record.finished_at is not None:
        wall = max(0.0, (record.finished_at - record.started_at).total_seconds())
    budget = record.spec.budget
    return CostSummary(
        total_usd=round(total, 6),
        budget_usd=budget.max_usd,
        budget_used_pct=round(100.0 * total / budget.max_usd, 1) if budget.max_usd else None,
        wall_clock_s=round(wall, 1),
        max_minutes=budget.max_minutes,
        n_calls=sum(int(r.get("n_calls") or 1) for r in rows),
        tokens=record.total_usage,
        by_stage=ordered,
        by_role=by_role,
        by_model=by_model,
        by_round=[{"index": r.index, "kind": r.kind, "cost_usd": round(r.usage.cost_usd, 6),
                   "seconds": round(r.duration_s, 1), "score": r.score} for r in record.rounds],
        ledger_usd=ledger,
        unattributed_usd=round(residual, 6),
        post_run_usd=round(max(0.0, ledger - total - residual), 6),
    )


# --------------------------------------------------------------------------- io
def write_usage_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    return write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))


def _place_usage_rows(ws: Workspace, rows: list[dict[str, Any]], source: str) -> str:
    """Write ``telemetry/usage.jsonl`` — or, when the run already carries a live
    ledger at its root, alias it there so only one physical copy exists."""
    if source == "live":
        rel = live_ledger_path(ws).name
        if ws.usage_path.exists() and not ws.usage_path.is_symlink():
            ws.usage_path.unlink()
        if not ws.usage_path.is_symlink():
            try:
                os.symlink(f"../{rel}", ws.usage_path)
            except OSError:  # no symlinks here: fall back to a copy
                write_usage_jsonl(ws.usage_path, rows)
        return rel
    write_usage_jsonl(ws.usage_path, rows)
    return ws.usage_path.name


def build_telemetry(ws: Workspace, record: RunRecord, *, write: bool = True) -> RunTelemetry:
    """Compute (and by default persist) ``telemetry/`` for one run."""
    rows, source = ledger_rows(ws)
    tele = RunTelemetry(
        settings=settings_snapshot(record),
        cost=cost_summary(record, rows),
        environment=dict(record.environment),
        files={"settings": "telemetry/settings.json",
               "cost": "telemetry/cost.json",
               "usage": "telemetry/usage.jsonl",
               "usage_source": source,
               "events": "telemetry/events.jsonl",
               "run_state": "telemetry/run_state.json",
               "stages": "telemetry/stages",
               "trajectories": "telemetry/trajectories"},
    )
    if write:
        ws.ensure_layout()
        write_json_atomic(ws.settings_path, tele.settings.model_dump(mode="json") if tele.settings else {})
        write_json_atomic(ws.cost_path, tele.cost.model_dump(mode="json") if tele.cost else {})
        _place_usage_rows(ws, rows, source)
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
