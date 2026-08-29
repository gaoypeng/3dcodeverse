"""Rebuild a cost ledger from an EXISTING run directory.

The harness has been recording ``Usage`` since day one, just not as a ledger:
per-call rows live in ``trajectories/*/transcript.jsonl``, session totals in
``result.json``, judge verdicts in ``record.rounds[].judgment.usage``, and the
plan / texture / pairwise costs only in ``events.jsonl``.  This module stitches
those into :class:`~codeverse.cost.types.CallCost` rows so the audit and
``3dcv cost`` work on runs recorded *before* the ledger existed.

Nothing here validates against the pydantic ``RunRecord``: old records must keep
loading even when the schema moves on, so everything is read as raw JSON.

Double-counting rules (same order as the row sources):

1. agent sessions — per-turn rows when a transcript has per-call usage, else one
   aggregate row from ``result.json``; the label+round is then *covered*;
2. gemini-cli sessions additionally carry the raw ``stats`` envelope in
   ``stdout.json`` — with ``recheck=True`` the row is rebuilt from it (per served
   model, correct cached/prompt split) instead of the stored ``Usage``;
3. judge verdicts — from the record's rounds (``rNN_cli.json`` re-judges are
   outside the run's budget and are skipped);
4. priced events (``plan.done``, ``texture.*``, ``pairwise.done``) that no row
   above covers — aggregates (``round.done`` / ``run.done`` / ``texture.done``)
   are skipped;
5. a final ``residual`` row for whatever ``record.total_usage`` still has left,
   so the ledger always reconciles to the recorded total.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.contracts.common import Usage
from codeverse.contracts.run import RunId
from codeverse.cost.ledger import price_call
from codeverse.cost.types import CallCost, Role, Stage, role_for_stage, stage_for_label
from codeverse.proc import read_json_or_none, read_jsonl_lenient

log = logging.getLogger(__name__)

#: events whose cost duplicates rows we already emit
_AGGREGATE_EVENTS = frozenset({"round.done", "run.done", "texture.done"})


def _read_json(path: Path) -> dict[str, Any]:
    return read_json_or_none(path, errors="replace") or {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl_lenient(path, dicts_only=True)


def _usage(d: Any) -> Usage:
    if isinstance(d, Usage):
        return d
    if not isinstance(d, dict):
        return Usage()
    return Usage(
        backend=str(d.get("backend") or ""),
        model=str(d.get("model") or ""),
        input_tokens=int(d.get("input_tokens") or 0),
        output_tokens=int(d.get("output_tokens") or 0),
        cached_tokens=int(d.get("cached_tokens") or 0),
        thoughts_tokens=int(d.get("thoughts_tokens") or 0),
        tool_calls=int(d.get("tool_calls") or 0),
        cost_usd=float(d.get("cost_usd") or 0.0),
        latency_ms=int(d.get("latency_ms") or 0),
    )


@dataclass
class RunLedger:
    """Rows of one run plus the metadata a cost report needs."""

    run: str = ""
    path: Path = Path()
    track: str = ""
    language: str = ""
    generator: str = ""
    status: str = ""
    stop_reason: str = ""
    passed: bool = False
    baseline_score: float | None = None
    final_score: float | None = None
    n_rounds: int = 0
    round_scores: list[float | None] = field(default_factory=list)
    gate_errors: list[int] = field(default_factory=list)
    recorded_usd: float = 0.0
    wall_s: float = 0.0   # time the run was actually working (budget elapsed / stage+round durations)
    span_s: float = 0.0   # first to last event — includes time queued behind other runs
    model_s: float = 0.0
    selected_candidate: str = ""   # "c1" when the run ran best-of-N and c1 won
    source: str = "reconstructed"  # live (the run wrote its own ledger) | reconstructed
    rows: list[CallCost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ledger_usd(self) -> float:
        return sum(r.cost_usd for r in self.rows)

    @property
    def harness_s(self) -> float:
        """Wall clock not spent waiting on a model (build / gates / render / IO).
        Clamped at 0: parallel stages can make ``model_s`` exceed the wall clock."""
        return max(0.0, self.wall_s - self.model_s)


def _row(usage: Usage, *, run: str, stage: Stage, label: str, rnd: int | None, source: str,
         role: Role | None = None, ts: float = 0.0, outcome: str = "ok", n_calls: int = 1,
         reprice: bool = False, backend: str = "", model: str = "") -> CallCost:
    cost, price_fields = price_call(usage, backend=backend or usage.backend,
                                    model=model or usage.model, reprice=reprice)
    return CallCost(
        ts=ts, run=run, round=rnd, stage=stage, role=role or role_for_stage(stage), label=label,
        input_tokens=usage.input_tokens, cached_tokens=usage.cached_tokens,
        output_tokens=usage.output_tokens, thoughts_tokens=usage.thoughts_tokens,
        tool_calls=usage.tool_calls, cost_usd=cost, recorded_usd=float(usage.cost_usd),
        latency_ms=usage.latency_ms,
        cache_hit=usage.cached_tokens > 0, outcome=outcome, n_calls=n_calls, source=source,
        **price_fields,
    )


# --------------------------------------------------------------------------- agent sessions
def _gemini_cli_usages(stdout: dict[str, Any]) -> list[Usage]:
    """Per served model from a gemini-cli ``stats`` envelope.  ``tokens.prompt`` is
    the TOTAL prompt (cached included) — sessions recorded before that fix stored
    ``tokens.input`` and under-billed by ~3-4x."""
    models = (((stdout.get("stats") or {}).get("models")) or {})
    out: list[Usage] = []
    for name, part in models.items():
        tok = (part or {}).get("tokens") or {}
        cached = int(tok.get("cached") or 0)
        prompt = tok.get("prompt")
        total = int(prompt) if prompt is not None else int(tok.get("input") or 0) + cached
        api = (part or {}).get("api") or {}
        out.append(Usage(
            backend="gemini-cli", model=str(name), input_tokens=total, cached_tokens=cached,
            output_tokens=int(tok.get("candidates") or 0), thoughts_tokens=int(tok.get("thoughts") or 0),
            tool_calls=int(((stdout.get("stats") or {}).get("tools") or {}).get("totalCalls") or 0),
            latency_ms=int(api.get("totalLatencyMs") or 0),
        ))
    return out


#: a retried / wrapped-up agent session records itself as ``<label>.a2`` / ``<label>.wrapup``
_ATTEMPT_SUFFIX = re.compile(r"\.(a\d+|wrapup)$")


def _base_label(label: str) -> str:
    """The *job* label a session label was derived from.

    A retry is recorded as ``<label>.a2``, a wrap-up as ``<label>.wrapup`` and a
    best-of-N candidate as ``c<k>:<label>``, while the ``generate.done`` event that
    summarises the whole job carries the plain job label.  De-duplication has to
    use this key: keying it on the session's own label left the retry's dollars in
    a different bucket, the job's pool ran dry, and the event was counted a SECOND
    time.  That artifact — not lost money — was most of the "$4.80 invisible to
    record.total_usage" in docs/COST.md §6; §6 now quotes the corrected figure."""
    lab = label.split(":", 1)[-1] if ":" in label else label
    return _ATTEMPT_SUFFIX.sub("", lab)


def _session_rows(d: Path, run: str, *, recheck: bool,
                  candidate: str = "") -> tuple[list[CallCost], str, int | None, float]:
    """Rows for one ``trajectories/<label>_rNN`` directory.  ``candidate`` is the
    ``_cand/c<k>`` sub-workspace this session belongs to (best-of-N), if any."""
    result = _read_json(d / "result.json")
    label = str(result.get("label") or d.name)
    rnd = result.get("round")
    rnd = int(rnd) if isinstance(rnd, (int, float)) else None
    stage = stage_for_label(str(result.get("kind") or "") if "repair" in str(result.get("kind") or "") else label)
    if candidate:
        stage, label = Stage.CANDIDATE, f"{candidate}:{label}"
    outcome = "ok" if result.get("ok") else str(result.get("exit_reason") or "error")
    duration = float(result.get("duration_s") or 0.0)
    session = _usage(result.get("usage"))
    rows: list[CallCost] = []

    if recheck and session.backend == "gemini-cli":
        parts = _gemini_cli_usages(_read_json(d / "stdout.json"))
        if parts:
            for part in parts:
                rows.append(_row(part, run=run, stage=stage, label=label, rnd=rnd,
                                 source="stdout", outcome=outcome, reprice=True))
            return rows, label, rnd, duration

    turns = [r for r in _read_jsonl(d / "transcript.jsonl") if isinstance(r.get("usage"), dict)]
    if turns:
        for turn in turns:
            rows.append(_row(_usage(turn["usage"]), run=run, stage=stage, label=label, rnd=rnd,
                             source="transcript", ts=float(turn.get("t") or 0.0), outcome=outcome,
                             reprice=recheck))
        return rows, label, rnd, duration
    if session.input_tokens or session.output_tokens or session.cost_usd:
        n_calls = int(result.get("turns") or 0) or 1
        rows.append(_row(session, run=run, stage=stage, label=label, rnd=rnd, source="trajectory",
                         outcome=outcome, n_calls=n_calls, reprice=recheck))
    return rows, label, rnd, duration


def _live_rows(root: Path, *, run: str, recheck: bool) -> list[CallCost]:
    """The run's own ledger rows, when it wrote one while it ran.

    Rows carry their run name from the writer; stamp the directory's name on any
    that do not (a battery copies runs around).  ``recheck`` re-prices them from
    today's table exactly like a reconstructed row."""
    try:
        from codeverse.cost.ledger import load_ledger

        rows = load_ledger(root)
    except Exception as e:  # noqa: BLE001 - a corrupt ledger falls back to reconstruction
        log.debug("cost: live ledger unreadable in %s: %s", root, e)
        return []
    out: list[CallCost] = []
    for row in rows:
        if not row.run:
            row.run = run
        if recheck:
            usage = Usage(backend=row.backend, model=row.model_id or row.model,
                          input_tokens=row.input_tokens, cached_tokens=row.cached_tokens,
                          output_tokens=row.output_tokens, thoughts_tokens=row.thoughts_tokens,
                          tool_calls=row.tool_calls, cost_usd=row.recorded_usd)
            cost, fields = price_call(usage, backend=row.backend, model=row.model_id or row.model,
                                      cache_write_tokens=row.cache_write_tokens, reprice=True)
            row = row.model_copy(update={"cost_usd": cost, **fields})
        out.append(row)
    return out


def _trajectory_dirs(root: Path) -> list[tuple[Path, str]]:
    """Every session directory of a run as ``(dir, candidate)``, including scene
    asset sub-workspaces (``_assets/<name>/trajectories/*``) and best-of-N
    candidates (``_cand/c*``, whose sessions are tagged with the candidate id)."""
    out: list[tuple[Path, str]] = []
    for traj in sorted(root.glob("trajectories")) + sorted(root.glob("_assets/*/trajectories")):
        out += [(p, "") for p in sorted(traj.iterdir()) if p.is_dir()]
    for traj in sorted(root.glob("_cand/*/trajectories")):
        cand = traj.parent.name
        out += [(p, cand) for p in sorted(traj.iterdir()) if p.is_dir()]
    return out


# --------------------------------------------------------------------------- one run
def reconstruct_run(run_dir: str | Path, *, recheck: bool = False) -> RunLedger:
    """Build the ledger of one run directory.

    A run that wrote its own ledger while it ran (``telemetry/cost.jsonl``, see
    :mod:`codeverse.cost.instrument`) is read from that ledger — one priced row
    per real call, nothing inferred.  Runs recorded before the ledger existed are
    rebuilt from ``trajectories/`` + ``record.json`` + ``events.jsonl`` exactly as
    before, so all 61 reference runs keep auditing.

    ``recheck=True`` re-prices every row with the CURRENT price table (and rebuilds
    gemini-cli rows from the raw CLI stats) — that is the audit view; the default
    keeps the dollars the run was actually billed."""
    root = Path(run_dir)
    record = _read_json(root / "record.json")
    spec = record.get("spec") or {}
    backends = spec.get("backends") or {}
    extra = record.get("extra") or {}
    total = _usage(record.get("total_usage"))
    run = root.name
    led = RunLedger(
        run=run, path=root,
        track=str(spec.get("track") or ""), language=str(spec.get("language") or ""),
        generator=str(backends.get("generator") or ""),
        status=str(record.get("status") or ""), stop_reason=str(extra.get("stop_reason") or ""),
        baseline_score=record.get("baseline_score"), final_score=record.get("final_score"),
        recorded_usd=float(total.cost_usd),
    )
    cands = _read_json(root / "rounds" / "candidates.json") or (extra.get("candidates") or {})
    if isinstance(cands, dict) and cands.get("selected") is not None:
        led.selected_candidate = f"c{cands['selected']}"
    rounds = record.get("rounds") or []
    led.n_rounds = len(rounds)
    led.passed = bool(record.get("status") == "passed")

    live_rows = _live_rows(root, run=run, recheck=recheck)
    led.source = "live" if live_rows else "reconstructed"
    led.rows += live_rows

    covered: dict[str, float] = {}  # JOB label (see _base_label) -> $ already accounted for by
    #                                 its session(s); a ``generate.done`` event within that
    #                                 budget is a duplicate, retries and wrap-ups included
    model_s = 0.0

    for d, cand in (() if live_rows else _trajectory_dirs(root)):
        rows, label, rnd, duration = _session_rows(d, run, recheck=recheck, candidate=cand)
        if rows:
            led.rows += rows
            key = _base_label(label)
            covered[key] = covered.get(key, 0.0) + sum(r.recorded_usd for r in rows)
            model_s += duration

    judged_rounds: set[int] = set()
    for r in rounds:
        idx = int(r.get("index") or 0)
        judgment = r.get("judgment") or {}
        led.round_scores.append((judgment or {}).get("overall"))
        led.gate_errors.append(sum(1 for g in (r.get("gates") or [])
                                   for f in (g.get("findings") or []) if f.get("severity") == "error"))
        ju = _usage(judgment.get("usage"))
        if ju.input_tokens or ju.cost_usd:
            judged_rounds.add(idx)
            model_s += ju.latency_ms / 1000.0
        if (ju.input_tokens or ju.cost_usd) and not live_rows:
            n = int(judgment.get("n_samples") or 1)
            degraded = bool((judgment.get("raw") or {}).get("degraded")) if isinstance(judgment.get("raw"), dict) else False
            led.rows.append(_row(ju, run=run, stage=Stage.JUDGE, label=str(judgment.get("rubric") or "judge"),
                                 rnd=idx, source="record", role=Role.JUDGE, n_calls=max(1, n),
                                 outcome="degraded" if degraded else "ok", reprice=recheck,
                                 backend=str(judgment.get("judge_backend") or ju.backend) or ju.backend,
                                 model=str(judgment.get("judge_backend") or ju.model) or ju.model))

    # ------------------------------------------------------------------ events
    first_t = last_t = 0.0
    active_s = 0.0
    for ev in _read_jsonl(root / "events.jsonl"):
        name = str(ev.get("event") or "")
        t = float(ev.get("t") or 0.0)
        first_t = first_t or t
        # folded in from a SECOND full pass over the same file, which added only this
        if name == "stage.done" and str(ev.get("stage")) == "plan":
            model_s += float(ev.get("duration_s") or 0.0)
        if name in ("run.done", "run.failed", "stop", "budget.exceeded"):
            last_t = max(last_t, t)   # a post-hoc texture pass is not part of the run's wall clock
        elif not last_t:
            last_t = t
        if name in ("stage.done", "round.done"):
            active_s += float(ev.get("duration_s") or 0.0)
        if name == "judge.done":
            model_s += float(ev.get("duration_s") or 0.0) if int(ev.get("round", -1)) not in judged_rounds else 0.0
        cost = ev.get("cost_usd")
        if live_rows or not isinstance(cost, (int, float)) or not cost or name in _AGGREGATE_EVENTS:
            continue
        rnd = ev.get("round")
        rnd = int(rnd) if isinstance(rnd, (int, float)) else None
        label = str(ev.get("label") or name.split(".")[0])
        if name == "generate.done":
            key = _base_label(label)
            if covered.get(key, 0.0) >= float(cost) - 0.0005:
                covered[key] = covered[key] - float(cost)
                continue             # a session already contributed this spend
        if name == "judge.done" and rnd in judged_rounds:
            continue
        stage = Stage.TEXTURE if name.startswith("texture") else stage_for_label(label)
        role = Role.IMAGE if name == "texture.generated" else role_for_stage(stage)
        model = str(ev.get("model") or "")
        usage = Usage(model=model, cost_usd=float(cost))
        row = _row(usage, run=run, stage=stage, label=label, rnd=rnd, source=f"event:{name}",
                   role=role, ts=t, model=model)
        row.price_source = "event-cost"  # cost known, tokens are not recorded by the event
        led.rows.append(row)
    led.span_s = max(0.0, last_t - first_t)
    elapsed_min = float(((extra.get("budget") or {}).get("elapsed_min")) or 0.0)
    # events are emitted from run creation, so the span of a queued bench run counts
    # hours it spent waiting for a worker slot; the stage/round clocks are the honest
    # ones (the budget's own elapsed restarts on ``3dcv resume``).
    led.wall_s = max(active_s, elapsed_min * 60.0) or led.span_s
    led.model_s = model_s

    # ------------------------------------------------------------------ residual
    if total.cost_usd:
        spent = sum(r.recorded_usd for r in led.rows)
        tok_in = total.input_tokens - sum(r.input_tokens for r in led.rows)
        tok_out = total.output_tokens - sum(r.output_tokens for r in led.rows)
        tok_cached = total.cached_tokens - sum(r.cached_tokens for r in led.rows)
        gap = total.cost_usd - spent
        if gap < -0.005:
            led.notes.append(f"${-gap:.4f} of ledger spend is outside record.total_usage "
                             "(post-run pass, or a round the budget cut before the total was written)")
        if gap > 0.005 or tok_in > 5000:
            usage = Usage(backend=total.backend, model=total.model,
                          input_tokens=max(0, tok_in), output_tokens=max(0, tok_out),
                          cached_tokens=max(0, tok_cached), cost_usd=max(0.0, gap))
            row = _row(usage, run=run, stage=Stage.OTHER, label="unattributed", rnd=None,
                       source="residual", role=Role.OTHER)
            row.cost_usd = row.recorded_usd = max(0.0, gap)
            row.price_source = "residual"
            led.rows.append(row)
    drift = led.ledger_usd - led.recorded_usd
    if recheck and abs(drift) > 0.005:
        led.notes.append(f"re-priced total differs from record.total_usage by ${drift:+.4f}")
    return led


# --------------------------------------------------------------------------- compare cells
def reconstruct_cell(cell_dir: str | Path, *, recheck: bool = False) -> RunLedger:
    """A ``bench/compare_backends.py`` one-shot cell (``gen/attempt*`` + ``eval/eval.json``)."""
    root = Path(cell_dir)
    cell = _read_json(root / "cell.json")
    run = RunId(battery="", rel=f"{root.parent.name}/{root.name}").slug  # <prompt>__<arm>
    led = RunLedger(run=run, path=root, track="static_object", language="blender",
                    generator=str(cell.get("arm") or root.name),
                    status=str(cell.get("status") or ""), passed=bool(cell.get("passed")),
                    final_score=cell.get("score"), n_rounds=1,
                    wall_s=float(cell.get("wall_s") or 0.0), model_s=float(cell.get("gen_seconds") or 0.0))
    live = _live_rows(root, run=run, recheck=recheck)
    if live:  # the cell wrote its own ledger while it ran: one priced row per real call
        led.source = "live"
        led.rows += live
        led.recorded_usd = float(cell.get("gen_cost_usd") or 0.0) + float(cell.get("judge_cost_usd") or 0.0)
        return led
    for attempt in sorted(root.glob("gen/attempt*")):
        result = _read_json(attempt / "result.json")
        usage = _usage(result.get("usage"))
        if usage.cost_usd or usage.output_tokens:
            led.rows.append(_row(usage, run=run, stage=Stage.BASELINE, label="oneshot", rnd=0,
                                 source="trajectory", outcome="ok" if result.get("ok") else "error",
                                 reprice=recheck))
    judgment = (_read_json(root / "eval" / "eval.json").get("judgment") or {})
    ju = _usage(judgment.get("usage"))
    if ju.cost_usd or ju.input_tokens:
        led.rows.append(_row(ju, run=run, stage=Stage.JUDGE, label=str(judgment.get("rubric") or "judge"),
                             rnd=0, source="record", role=Role.JUDGE,
                             n_calls=int(judgment.get("n_samples") or 1), reprice=recheck))
    led.recorded_usd = float(cell.get("gen_cost_usd") or 0.0) + float(cell.get("judge_cost_usd") or 0.0)
    return led


def find_runs(root: str | Path) -> list[Path]:
    """Every run directory under ``root`` (a dir with ``record.json``), plus one-shot
    compare cells (a dir with ``cell.json`` and no ``run/record.json``).  Nested
    sub-workspaces (``_assets/``, ``_cand/``) are part of their parent, not runs."""
    base = Path(root)
    if (base / "record.json").is_file():
        return [base]
    out: list[Path] = []
    for path in sorted(base.rglob("record.json")):
        rel = path.parent.relative_to(base).parts
        if any(p in ("_assets", "_cand") for p in rel):
            continue
        d = path.parent
        if d.name == "run" and (d.parent / "cell.json").is_file():
            d = d.parent   # a compare-battery cell: name it <prompt>/<arm>, not "run"
        out.append(d)
    known = {str(p) for p in out}
    for cell in sorted(base.rglob("cell.json")):
        d = cell.parent
        if str(d) in known or (d / "run" / "record.json").is_file():
            continue
        if any(p in ("_assets", "_cand") for p in d.relative_to(base).parts):
            continue
        out.append(d)
    return out


def reconstruct(path: str | Path, *, recheck: bool = False) -> RunLedger:
    """Dispatch on what the directory looks like (run record vs compare cell)."""
    p = Path(path)
    if (p / "record.json").is_file():
        return reconstruct_run(p, recheck=recheck)
    if (p / "run" / "record.json").is_file():
        led = reconstruct_run(p / "run", recheck=recheck)
        led.run = RunId(battery="", rel=f"{p.parent.name}/{p.name}").slug  # <prompt>__<arm>
        # a compare cell also spends OUTSIDE the harness run it wraps: the fixed
        # evaluator's judge, and a repair arm's extra generations.  `run_cell` opens a
        # ledger for the cell itself (nested around the run's own), and that spend is
        # exactly the §6 gap the audit sees on the two cmp_easy_stool harness cells.
        led.rows += _live_rows(p, run=led.run, recheck=recheck)
        return led
    return reconstruct_cell(p, recheck=recheck)
