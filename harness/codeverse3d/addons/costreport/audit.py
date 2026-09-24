"""Cost audit over a directory of runs: where the money and the minutes go.

``audit_runs(paths)`` reads every run's own ledger (``telemetry/cost.jsonl``, the one record
of money — docs/COST.md §12) and its record, stamps every row with the run's track /
language / status, and aggregates:

* $ per run, per track, per stage, per role, per backend, per model;
* token composition (input / cached / output / thoughts) and the effective
  $/1k-token rate each bucket actually paid;
* time next to the money — the run's minutes (``RunRecord.minutes``) and its model time;
* **waste**: repair loops that never got their round to build, best-of-N candidates that
  lost, and spend in a round the clock cut.  Nothing is waste for scoring below another
  round: since 2026-09-22 every round is kept and any of them can be the one handed over.

A directory with no ledger has no rows and is left out: the reconstruction of pre-ledger
runs from their trajectories, verdicts and events went on 2026-09-22 (434 of 467 archived
runs carry a ledger; the 33 without are five v1 batteries outside the repo).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.run import RunId
from codeverse3d.cost.ledger import load_ledger, price_call, summarise
from codeverse3d.cost.types import CallCost, Stage, Summary
from codeverse3d.proc import read_json_or_none
from codeverse3d.record.record import (
    RecordError,
    find_run_dirs,
    is_run_dir,
    load_record,
    stop_reason,
)


@dataclass
class RunLedger:
    """One run's (or compare cell's) ledger rows plus what a cost report says about it."""

    run: str = ""
    path: Path = Path()
    track: str = ""
    language: str = ""
    status: str = ""
    stop_reason: str = ""
    n_rounds: int = 0
    round_built: list[bool | None] = field(default_factory=list)  # per round: did its build (+repair) succeed
    selected_candidate: str = ""  # "c1" when the run ran best-of-N and c1 won
    minutes: float | None = None  # RunRecord.minutes (a one-shot cell: its cell.json minutes)
    cell_score: float | None = None  # a one-shot compare cell's one score (it has no record)
    written_usd: float = 0.0  # the rows as written, before a --recheck re-priced them
    rows: list[CallCost] = field(default_factory=list)

    @property
    def ledger_usd(self) -> float:
        return sum(r.cost_usd for r in self.rows)

    @property
    def model_s(self) -> float:
        return sum(r.latency_ms for r in self.rows) / 1000.0


def _rows(root: Path, led: RunLedger, *, recheck: bool) -> None:
    """Add ``root``'s own ledger rows to ``led`` — stamped with its run name where a battery
    copied them without one, and re-priced from today's table when ``recheck``."""
    for row in load_ledger(root):
        led.written_usd += row.cost_usd
        row.run = row.run or led.run
        if recheck:
            usage = Usage(backend=row.backend, model=row.model_id or row.model, input_tokens=row.input_tokens,
                          cached_tokens=row.cached_tokens, output_tokens=row.output_tokens,
                          thoughts_tokens=row.thoughts_tokens, tool_calls=row.tool_calls, cost_usd=row.recorded_usd)
            cost, fields = price_call(usage, backend=row.backend, model=row.model_id or row.model,
                                      cache_write_tokens=row.cache_write_tokens, reprice=True)
            row = row.model_copy(update={"cost_usd": cost, **fields})
        led.rows.append(row)


def read_run(run_dir: Path, *, name: str = "", recheck: bool = False) -> RunLedger:
    """A run directory: its ledger rows and its record's facts (an unreadable record keeps the rows)."""
    led = RunLedger(run=name or run_dir.name, path=run_dir)
    try:
        rec = load_record(run_dir)
    except RecordError:
        rec = None
    if rec is not None:
        led.track, led.language, led.status = rec.spec.track.value, rec.spec.language.value, rec.status.value
        led.stop_reason = stop_reason(rec)
        led.n_rounds, led.minutes = len(rec.rounds), rec.minutes
        led.round_built = [None if r.build is None else r.build.ok for r in rec.rounds]
        cands = read_json_or_none(run_dir / "rounds" / "candidates.json") or rec.extra.get("candidates") or {}
        if isinstance(cands, dict) and cands.get("selected") is not None:
            led.selected_candidate = f"c{cands['selected']}"
    _rows(run_dir, led, recheck=recheck)
    return led


def read_cell(cell_dir: Path, *, name: str, recheck: bool = False) -> RunLedger:
    """A ``compare_backends`` / ``ab_plan`` cell.  A harness arm is its run plus the cell's own
    ledger (the fixed evaluator's judge, a repair arm's extra generations); a one-shot or
    bare-agent arm is the cell ledger alone, described by ``cell.json`` and ``eval/spec.json``."""
    if (cell_dir / "run" / "record.json").is_file():
        led = read_run(cell_dir / "run", name=name, recheck=recheck)
    else:
        cell = read_json_or_none(cell_dir / "cell.json") or {}
        spec = read_json_or_none(cell_dir / "eval" / "spec.json") or {}
        minutes = cell.get("minutes")
        led = RunLedger(run=name, path=cell_dir, track=str(spec.get("track") or ""),
                        language=str(spec.get("language") or ""), status=str(cell.get("status") or ""),
                        n_rounds=1, round_built=[cell.get("build_ok")], cell_score=cell.get("score"),
                        minutes=float(cell.get("wall_s") or 0.0) / 60 if minutes is None else float(minutes))
    _rows(cell_dir, led, recheck=recheck)
    return led


def find_runs(root: str | Path) -> list[Path]:
    """Every run directory under ``root`` (a dir with ``record.json``) and every compare cell
    (a dir with ``cell.json``; one that wraps a harness run is named by the cell, not its
    ``run/``), found by ``record.find_run_dirs``: once each — a cell another battery symlinks in
    included — and never inside a run (its ``_assets/``, ``_cand/`` sub-workspaces are part of it)."""
    base = Path(root)
    if _is_run_or_cell(base):
        return [base]
    return find_run_dirs(base, predicate=_is_run_or_cell)


def _is_run_or_cell(d: Path) -> bool:
    return is_run_dir(d) or (d / "cell.json").is_file()


#: dimensions the audit always aggregates on
AUDIT_DIMENSIONS = ("run", "track", "language", "stage", "role", "backend", "model", "provider",
                    "status", "outcome", "source", "key")


@dataclass
class WasteItem:
    """One dollar that bought nothing (or bought a worse artifact)."""

    kind: str  # repair_no_converge | lost_candidate | post_budget
    run: str
    usd: float
    detail: str = ""
    round: int | None = None


@dataclass
class Audit:
    """Everything the report needs; ``summary`` holds the bucket maps."""

    runs: list[RunLedger] = field(default_factory=list)
    rows: list[CallCost] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)
    waste: list[WasteItem] = field(default_factory=list)

    # ---------------------------------------------------------------- totals
    @property
    def total_usd(self) -> float:
        return self.summary.total.cost_usd

    @property
    def written_usd(self) -> float:
        return sum(r.written_usd for r in self.runs)

    @property
    def n_runs(self) -> int:
        return len(self.runs)

    @property
    def usd_per_run(self) -> float:
        return self.total_usd / self.n_runs if self.n_runs else 0.0

    @property
    def minutes(self) -> float:
        return sum(r.minutes or 0.0 for r in self.runs)

    @property
    def model_s(self) -> float:
        return sum(r.model_s for r in self.runs)

    def waste_total(self) -> float:
        return sum(w.usd for w in self.waste)

    def waste_by_kind(self) -> dict[str, tuple[int, float]]:
        out: dict[str, tuple[int, float]] = {}
        for w in self.waste:
            n, usd = out.get(w.kind, (0, 0.0))
            out[w.kind] = (n + 1, usd + w.usd)
        return dict(sorted(out.items(), key=lambda kv: -kv[1][1]))

    def calls_per_round(self) -> float:
        rounds = {(r.run, r.round) for r in self.rows if r.round is not None}
        calls = sum(r.n_calls for r in self.rows if r.round is not None)
        return calls / len(rounds) if rounds else 0.0


def _waste(led: RunLedger) -> list[WasteItem]:
    items: list[WasteItem] = []
    # repair loops that never got their round to build
    unbuilt = {i for i, ok in enumerate(led.round_built) if ok is False}
    repair = [r for r in led.rows if r.stage is Stage.REPAIR and r.round in unbuilt]
    if repair:
        items.append(WasteItem("repair_no_converge", led.run, sum(r.cost_usd for r in repair),
                               detail=f"{len(repair)} repair call(s) in round(s) "
                                      f"{', '.join(f'r{i:02d}' for i in sorted({r.round for r in repair}))} "
                                      f"that still did not build"))
    # best-of-N candidates that lost (their whole sub-workspace is thrown away): the candidate
    # clone's sessions are labelled ``baseline_c<k>``.  Generator sessions only — the
    # quick-judge rows inside a candidate carry no candidate marker.
    won = led.selected_candidate
    losers = [r for r in led.rows if r.stage is Stage.CANDIDATE and not (won and r.label.endswith(f"_{won}"))]
    if losers:
        names = sorted({r.label.rsplit("_", 1)[-1] for r in losers})
        items.append(WasteItem("lost_candidate", led.run, sum(r.cost_usd for r in losers),
                               detail=f"best-of-N: {', '.join(names)} lost to {won or '(unknown)'}"))
    # anything spent on a round that finished after the budget was blown
    if led.stop_reason in ("budget",) or led.status == "budget":
        extra = [r for r in led.rows if r.round is not None and led.n_rounds and r.round > led.n_rounds - 1]
        if extra:
            items.append(WasteItem("post_budget", led.run, sum(r.cost_usd for r in extra),
                                   detail=f"round r{extra[0].round:02d} completed after the budget was already gone"))
    return items


def audit_runs(paths: Iterable[str | Path], *, recheck: bool = False) -> Audit:
    """Read + aggregate every run and cell under ``paths`` (run dirs or trees).  A run is named
    by its place under the path it was found from (``RunId``), so an A/B battery's control and
    variant cells of one prompt stay two runs."""
    audit = Audit()
    for root in map(Path, paths):
        for d in find_runs(root):
            name = RunId(battery="", rel=d.relative_to(root).as_posix() if d != root else d.name).slug
            led = read_cell(d, name=name, recheck=recheck) if (d / "cell.json").is_file() else \
                read_run(d, name=name, recheck=recheck)
            _add(audit, led)
    audit.summary = summarise(audit.rows, dimensions=AUDIT_DIMENSIONS)
    audit.runs.sort(key=lambda r: -r.ledger_usd)
    audit.waste.sort(key=lambda w: -w.usd)
    return audit


def _add(audit: Audit, led: RunLedger) -> None:
    if not led.rows:
        return
    for row in led.rows:
        row.track = led.track or "?"
        row.language = led.language or "?"
        row.status = led.status or "?"
    audit.runs.append(led)
    audit.rows += led.rows
    audit.waste += _waste(led)


# --------------------------------------------------------------------------- derived views
def cached_input_share(audit: Audit) -> tuple[int, int, float]:
    """``(cached, total input, $ paid for cache reads)`` — how much of every prompt
    is context we have already sent before."""
    cached = audit.summary.total.cached_tokens
    total = audit.summary.total.input_tokens
    usd = sum(min(r.cached_tokens, r.input_tokens) * r.price_cached / 1e6 for r in audit.rows)
    return cached, total, usd


def uncached_if_no_cache(audit: Audit) -> float:
    """What the same traffic would have cost with a 0% cache-hit rate — the
    measured value of prompt caching as it stands today."""
    extra = 0.0
    for r in audit.rows:
        cached = min(r.cached_tokens, r.input_tokens)
        extra += cached * max(0.0, r.price_input - r.price_cached) / 1e6
    return audit.total_usd + extra


def stage_latency(audit: Audit) -> dict[str, tuple[float, float]]:
    """stage → (model seconds, share of all model seconds)."""
    per: dict[str, float] = {}
    for r in audit.rows:
        per[str(r.stage)] = per.get(str(r.stage), 0.0) + r.latency_ms / 1000.0
    total = sum(per.values()) or 1.0
    return {k: (v, v / total) for k, v in sorted(per.items(), key=lambda kv: -kv[1])}


def price_confidence(audit: Audit) -> dict[str, float]:
    """$ by how trustworthy the price row behind it is."""
    out: dict[str, float] = {}
    for r in audit.rows:
        key = r.price_source if r.price_source in ("unknown", "provider-reported") \
            else ("approximate" if r.price_approximate else "verified")
        out[key] = out.get(key, 0.0) + r.cost_usd
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
