"""Cost audit over a directory of runs: where the money and the minutes go."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from codeverse3d.contracts.common import Usage
from codeverse3d.proc import read_json_or_none

from c3d_flywheel.cost.ledger import load_ledger, price_call, summarise
from c3d_flywheel.cost.types import CallCost, Stage, Summary
from c3d_flywheel.legacy import RunId
from c3d_flywheel.record.record import (
    RecordError,
    find_run_dirs,
    is_run_dir,
    load_record,
    stop_reason,
)


@dataclass
class RunLedger:

    run: str = ""
    path: Path = Path()
    track: str = ""
    language: str = ""
    status: str = ""
    stop_reason: str = ""
    n_rounds: int = 0
    round_built: list[bool | None] = field(default_factory=list)
    minutes: float | None = None
    cell_score: float | None = None
    written_usd: float = 0.0
    rows: list[CallCost] = field(default_factory=list)

    @property
    def ledger_usd(self) -> float:
        return sum(r.cost_usd for r in self.rows)

    @property
    def model_s(self) -> float:
        return sum(r.latency_ms for r in self.rows) / 1000.0


def _rows(root: Path, led: RunLedger, *, recheck: bool) -> None:
    for row in load_ledger(root):
        led.written_usd += row.cost_usd
        row.run = row.run or led.run
        if recheck:
            usage = Usage(backend=row.backend, model=row.model_id or row.model, input_tokens=row.input_tokens,
                          cached_tokens=row.cached_tokens, output_tokens=row.output_tokens,
                          thoughts_tokens=row.thoughts_tokens, tool_calls=row.tool_calls, cost_usd=row.recorded_usd)
            cost, fields = price_call(usage, backend=row.backend, model=row.model_id or row.model,
                                      reprice=True)
            row = row.model_copy(update={"cost_usd": cost, **fields})
        led.rows.append(row)


def read_run(run_dir: Path, *, name: str = "", recheck: bool = False) -> RunLedger:
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
    _rows(run_dir, led, recheck=recheck)
    return led


def read_cell(cell_dir: Path, *, name: str, recheck: bool = False) -> RunLedger:
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
    base = Path(root)
    if _is_run_or_cell(base):
        return [base]
    return find_run_dirs(base, predicate=_is_run_or_cell)


def _is_run_or_cell(d: Path) -> bool:
    return is_run_dir(d) or (d / "cell.json").is_file()


AUDIT_DIMENSIONS = ("run", "track", "language", "stage", "role", "backend", "model", "provider",
                    "status", "outcome", "source", "key")


@dataclass
class WasteItem:

    kind: str
    run: str
    usd: float
    detail: str = ""
    round: int | None = None


@dataclass
class Audit:

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
    unbuilt = {i for i, ok in enumerate(led.round_built) if ok is False}
    repair = [r for r in led.rows if r.stage is Stage.REPAIR and r.round in unbuilt]
    if repair:
        items.append(WasteItem("repair_no_converge", led.run, sum(r.cost_usd for r in repair),
                               detail=f"{len(repair)} repair call(s) in round(s) "
                                      f"{', '.join(f'r{i:02d}' for i in sorted({r.round for r in repair}))} "
                                      f"that still did not build"))
    if led.stop_reason in ("budget",) or led.status == "budget":
        extra = [r for r in led.rows if r.round is not None and led.n_rounds and r.round > led.n_rounds - 1]
        if extra:
            items.append(WasteItem("post_budget", led.run, sum(r.cost_usd for r in extra),
                                   detail=f"round r{extra[0].round:02d} completed after the budget was already gone"))
    return items


def audit_runs(paths: Iterable[str | Path], *, recheck: bool = False) -> Audit:
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
    cached = audit.summary.total.cached_tokens
    total = audit.summary.total.input_tokens
    usd = sum(min(r.cached_tokens, r.input_tokens) * r.price_cached / 1e6 for r in audit.rows)
    return cached, total, usd


def uncached_if_no_cache(audit: Audit) -> float:
    extra = 0.0
    for r in audit.rows:
        cached = min(r.cached_tokens, r.input_tokens)
        extra += cached * max(0.0, r.price_input - r.price_cached) / 1e6
    return audit.total_usd + extra


def stage_latency(audit: Audit) -> dict[str, tuple[float, float]]:
    per: dict[str, float] = {}
    for r in audit.rows:
        per[str(r.stage)] = per.get(str(r.stage), 0.0) + r.latency_ms / 1000.0
    total = sum(per.values()) or 1.0
    return {k: (v, v / total) for k, v in sorted(per.items(), key=lambda kv: -kv[1])}


def price_confidence(audit: Audit) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in audit.rows:
        key = r.price_source if r.price_source in ("unknown", "provider-reported") \
            else ("approximate" if r.price_approximate else "verified")
        out[key] = out.get(key, 0.0) + r.cost_usd
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
