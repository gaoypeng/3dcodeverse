"""Render an :class:`~codeverse3d.addons.costreport.audit.Audit` as markdown or a console table.

Pure formatting: every number comes from the audit, nothing is recomputed here — the
per-run baseline / picked scores come from ``addons/select`` (a one-shot compare cell,
which has no record.json, reports its one score).
"""

from __future__ import annotations

from collections.abc import Sequence

from codeverse3d.addons import select
from codeverse3d.addons.costreport.audit import (
    Audit,
    RunLedger,
    cached_input_share,
    price_confidence,
    stage_latency,
    uncached_if_no_cache,
)
from codeverse3d.cost.types import CostBucket

STAGE_ORDER = ("plan", "skeleton", "assets", "env", "zones", "assemble", "baseline", "candidate",
               "repair", "refine", "gates", "render", "judge", "pairwise", "texture", "caption", "other")


def _usd(x: float) -> str:
    return f"${x:,.4f}" if abs(x) < 1 else f"${x:,.2f}"


def _share(part: float, whole: float) -> float:
    """``part`` as a percentage of ``whole``; 0 when the whole is 0 (a run that booked no money — every
    session killed before it reported usage — is still a run to report)."""
    return 100 * part / whole if whole else 0.0


def _tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return str(n)


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def bucket_rows(buckets: Sequence[CostBucket], total: float) -> list[list[object]]:
    rows: list[list[object]] = []
    for b in buckets:
        rows.append([
            b.key, _usd(b.cost_usd), f"{100 * b.cost_usd / total:.1f}%" if total else "-",
            f"{b.n_calls:,}", _tok(b.input_tokens), f"{100 * b.cached_fraction:.0f}%",
            _tok(b.output_tokens), f"${b.usd_per_1k_tokens:.5f}", f"{b.latency_ms / 3.6e6:.1f} h",
            f"{b.attempts_per_call:.2f}" if b.n_attempted else "-",
        ])
    return rows


BUCKET_HEADERS = ("key", "USD", "share", "calls", "input", "cached", "output", "$/1k tok", "model time",
                  "tries/call")


def keyed_buckets(audit: Audit) -> list[CostBucket]:
    """Per-API-key buckets (``CallCost.key``), most calls first; empty for ledgers
    written before the key suffix was recorded (2026-08-26)."""
    return sorted((b for b in audit.summary.dimension("key").values() if b.key != "(none)"),
                  key=lambda b: -b.n_calls)


def dimension_table(audit: Audit, dim: str, *, limit: int | None = None, order: Sequence[str] = ()) -> str:
    buckets = audit.summary.ranked(dim, limit=limit)
    if order:
        rank = {k: i for i, k in enumerate(order)}
        buckets.sort(key=lambda b: (rank.get(b.key, len(rank)), -b.cost_usd))
    return table(BUCKET_HEADERS, bucket_rows(buckets, audit.total_usd))


def _scores(r: RunLedger) -> tuple[float | None, float | None]:
    """``(baseline, picked)``: round 0's score and the round ``addons/select`` picks."""
    if (r.path / "record.json").is_file():
        try:
            s = select.summarise(r.path)
            return s.baseline_score, s.picked_score
        except Exception:  # noqa: BLE001 - an unreadable record still gets its cost row
            return None, None
    return r.cell_score, r.cell_score  # a one-shot cell: one round


def runs_table(audit: Audit, *, limit: int = 20) -> str:
    rows = []
    for r in audit.runs[:limit]:
        baseline, picked = _scores(r)
        rows.append([r.run, r.track or "-", _usd(r.ledger_usd), r.n_rounds,
                     f"{baseline:.3f}" if baseline is not None else "-",
                     f"{picked:.3f}" if picked is not None else "-",
                     r.stop_reason or r.status, "-" if r.minutes is None else f"{r.minutes:.1f}",
                     f"{r.model_s / 60:.1f}"])
    return table(("run", "track", "USD", "rounds", "baseline", "picked", "stop", "minutes", "model min"), rows)


def waste_table(audit: Audit) -> str:
    rows = [[k, n, _usd(usd), f"{_share(usd, audit.total_usd):.1f}%"]
            for k, (n, usd) in audit.waste_by_kind().items()]
    rows.append(["**total**", sum(n for n, _ in audit.waste_by_kind().values()),
                 _usd(audit.waste_total()), f"{_share(audit.waste_total(), audit.total_usd):.1f}%"])
    return table(("waste", "n", "USD", "share of spend"), rows)


def summary_lines(audit: Audit) -> list[str]:
    cached, total_in, cache_usd = cached_input_share(audit)
    no_cache = uncached_if_no_cache(audit)
    return [
        f"- runs: **{audit.n_runs}** — total **{_usd(audit.total_usd)}**, {_usd(audit.usd_per_run)} per run",
        f"- tokens: {_tok(total_in)} input of which **{_share(cached, total_in):.0f}% cached** "
        f"({_tok(audit.summary.total.output_tokens)} output, {_tok(audit.summary.total.thoughts_tokens)} thoughts)",
        f"- prompt caching already saves **{_usd(no_cache - audit.total_usd)}** "
        f"({_share(no_cache - audit.total_usd, no_cache):.0f}% of what this traffic would cost uncached); "
        f"cache reads still cost {_usd(cache_usd)}",
        f"- time: {audit.minutes / 60:.1f} h of run steps (provider errors left out), "
        f"{audit.model_s / 3600:.1f} h of model calls (side by side ones counted each)",
        f"- calls: {audit.summary.total.n_calls:,} model calls, {audit.calls_per_round():.0f} per round",
        f"- identified waste: **{_usd(audit.waste_total())}** ({_share(audit.waste_total(), audit.total_usd):.0f}% of spend)",
    ]


def markdown(audit: Audit, *, title: str = "Cost audit") -> str:
    parts: list[str] = [f"# {title}", ""]
    parts += summary_lines(audit)
    for dim, heading, order in (("stage", "Per stage", STAGE_ORDER), ("role", "Per role", ()),
                                ("track", "Per track", ()), ("backend", "Per backend", ()),
                                ("model", "Per model", ())):
        parts += ["", f"## {heading}", "", dimension_table(audit, dim, order=order)]
    if keyed_buckets(audit):
        parts += ["", "## Per API key (last 4 chars)", "", dimension_table(audit, "key")]
    parts += ["", "## Most expensive runs", "", runs_table(audit)]
    parts += ["", "## Where a dollar bought nothing", "", waste_table(audit)]
    parts += ["", "## Model time per stage", "",
              table(("stage", "model hours", "share"),
                    [[k, f"{v / 3600:.2f}", f"{share * 100:.1f}%"] for k, (v, share) in stage_latency(audit).items()])]
    parts += ["", "## Price confidence", "",
              table(("price source", "USD"), [[k, _usd(v)] for k, v in price_confidence(audit).items()])]
    return "\n".join(parts) + "\n"


def console(audit: Audit) -> str:
    """Compact plain-text version for ``3dcode cost``."""
    lines = [line.replace("**", "") for line in summary_lines(audit)]
    lines += ["", "stage:"]
    for b in audit.summary.ranked("stage"):
        lines.append(f"  {b.key:<12} {_usd(b.cost_usd):>10}  {_share(b.cost_usd, audit.total_usd):5.1f}%  "
                     f"{b.n_calls:>6,} calls  cached {100 * b.cached_fraction:3.0f}%")
    lines += ["", "role:"]
    for b in audit.summary.ranked("role"):
        lines.append(f"  {b.key:<12} {_usd(b.cost_usd):>10}  {_share(b.cost_usd, audit.total_usd):5.1f}%")
    if keyed := keyed_buckets(audit):
        lines += ["", "key (last 4 chars):"]
        for b in keyed:
            lines.append(f"  {b.key:<8} {b.n_calls:>6,} calls  {_usd(b.cost_usd):>10}  "
                         f"tries/call {b.attempts_per_call:.2f}  model time {b.latency_ms / 6e4:.0f} min")
    lines += ["", "waste:"]
    for k, (n, usd) in audit.waste_by_kind().items():
        lines.append(f"  {k:<20} {n:>3}  {_usd(usd):>10}")
    return "\n".join(lines)
