"""Cost audit over a directory of runs: where the money and the minutes go.

``audit_runs(paths)`` reconstructs a ledger per run (``reconstruct.py``),
stamps every row with its run's track / language / status, and aggregates:

* $ per run, per track, per stage, per role, per backend, per model;
* token composition (input / cached / output / thoughts) and the effective
  $/1k-token rate each bucket actually paid;
* latency next to the money — wall clock, model time, harness (non-model) time;
* **waste**: repair loops that never got their round to build, degraded verdicts,
  best-of-N candidates that lost, and spend after the budget was already blown.
  Nothing is waste for scoring below another round: since 2026-09-22 every round is
  kept and any of them can be the one handed over (the "regression", "zero_delta_round"
  and "unpromoted_judge" kinds measured against an in-run best that no longer exists).

Everything is measured from the recorded telemetry; nothing is estimated except
where a row says so (``price_source == "event-cost"`` — the event recorded a
cost but no tokens).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from codeverse3d.cost.ledger import summarise
from codeverse3d.cost.reconstruct import RunLedger, find_runs, reconstruct
from codeverse3d.cost.types import CallCost, Role, Stage, Summary

#: dimensions the audit always aggregates on
AUDIT_DIMENSIONS = ("run", "track", "language", "stage", "role", "backend", "model", "provider",
                    "status", "outcome", "source", "key")


@dataclass
class WasteItem:
    """One dollar that bought nothing (or bought a worse artifact)."""

    kind: str  # repair_no_converge | degraded_judge | lost_candidate | post_budget
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
    def recorded_usd(self) -> float:
        return sum(r.recorded_usd for r in self.runs)

    @property
    def n_runs(self) -> int:
        return len(self.runs)

    @property
    def usd_per_run(self) -> float:
        return self.total_usd / self.n_runs if self.n_runs else 0.0

    @property
    def wall_s(self) -> float:
        return sum(r.wall_s for r in self.runs)

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
    for row in led.rows:
        if row.outcome == "degraded" and (row.stage is Stage.JUDGE or row.role is Role.JUDGE):
            items.append(WasteItem("degraded_judge", led.run, row.cost_usd, round=row.round,
                                   detail="degraded verdict (glitch, not a score) — paid for, not usable"))
    # repair loops that never got their round to build
    unbuilt = {i for i, ok in enumerate(led.round_built) if ok is False}
    repair = [r for r in led.rows if r.stage is Stage.REPAIR and r.round in unbuilt]
    if repair:
        items.append(WasteItem("repair_no_converge", led.run, sum(r.cost_usd for r in repair),
                               detail=f"{len(repair)} repair call(s) in round(s) "
                                      f"{', '.join(f'r{i:02d}' for i in sorted({r.round for r in repair}))} "
                                      f"that still did not build"))
    # best-of-N candidates that lost (their whole sub-workspace is thrown away).  Two label
    # forms: a live ledger row is ``baseline_c<k>`` (the candidate clone's label), a
    # reconstructed one ``c<k>:baseline``.  Generator sessions only — the quick-judge rows
    # inside a candidate carry no candidate marker on either path.
    won = led.selected_candidate
    losers = [r for r in led.rows if r.stage is Stage.CANDIDATE
              and not (won and (r.label.startswith(f"{won}:") or r.label.endswith(f"_{won}")))]
    if losers:
        names = sorted({_candidate_of(r.label) for r in losers})
        items.append(WasteItem("lost_candidate", led.run, sum(r.cost_usd for r in losers),
                               detail=f"best-of-N: {', '.join(names)} lost to {won or '(unknown)'}"))
    # anything spent on a round that finished after the budget was blown
    if led.stop_reason in ("budget",) or led.status == "budget":
        extra = [r for r in led.rows if r.round is not None and led.n_rounds and r.round > led.n_rounds - 1]
        if extra:
            items.append(WasteItem("post_budget", led.run, sum(r.cost_usd for r in extra),
                                   detail=f"round r{extra[0].round:02d} completed after the budget was already gone"))
    return items


def _candidate_of(label: str) -> str:
    """``c1:baseline`` → ``c1``; ``baseline_c1`` → ``c1``."""
    head, sep, _ = label.partition(":")
    return head if sep else label.rsplit("_", 1)[-1]


def audit_runs(paths: Iterable[str | Path], *, recheck: bool = False) -> Audit:
    """Reconstruct + aggregate every run under ``paths`` (files, run dirs or trees)."""
    dirs: list[Path] = []
    for p in paths:
        dirs += find_runs(p)
    audit = Audit()
    for d in dirs:
        led = reconstruct(d, recheck=recheck)
        if not led.rows:
            continue
        for row in led.rows:
            row.track = led.track or "?"
            row.language = led.language or "?"
            row.status = led.status or "?"
        audit.runs.append(led)
        audit.rows += led.rows
        audit.waste += _waste(led)
    audit.summary = summarise(audit.rows, dimensions=AUDIT_DIMENSIONS)
    audit.runs.sort(key=lambda r: -r.ledger_usd)
    audit.waste.sort(key=lambda w: -w.usd)
    return audit


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
        key = r.price_source if r.price_source in ("unknown", "provider-reported", "event-cost", "residual") \
            else ("approximate" if r.price_approximate else "verified")
        out[key] = out.get(key, 0.0) + r.cost_usd
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
