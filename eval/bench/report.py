"""Aggregate a bench run: per-tier / per-category stats → markdown + HTML gallery.

``build_report(out_dir)`` reads ``results.jsonl`` (or results.json), aggregates
baseline / picked-round mean + median, the delta and cost per tier and category (no pass
rate: a run is not passed or failed since 2026-09-22), writes ``report.md`` and
``report.html`` — the same self-contained page as ``3dcode gallery build --embed``
(``codeverse3d.addons.gallery``), one section per tier, with the stats tables under the
summary strip.
"""

from __future__ import annotations

import html
import json
import statistics
from pathlib import Path

from pydantic import BaseModel, Field

from bench._compare_report import _f as _fmt
from bench._compare_report import _mean
from bench._jsonl import latest, read_jsonl
from bench.run_bench import BenchItemResult
from codeverse3d.addons.gallery import GalleryIndex, RootSection, RunEntry, render_static
from codeverse3d.addons.gallery.index import entry_from_record
from codeverse3d.record.record import RecordError, load_record
from codeverse3d.workspace import Workspace

TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2}


class GroupStats(BaseModel):
    group: str
    n: int
    n_scored: int
    n_evaluated: int = Field(default=0, description="rows that actually ran (n minus provider outages)")
    infra_failed: int = Field(default=0, description="rows dropped: the provider, not the model, failed")
    baseline_mean: float | None
    picked_mean: float | None
    picked_median: float | None
    delta_mean: float | None
    cost_mean: float
    minutes_mean: float
    errors: int


class BenchReport(BaseModel):
    name: str
    n: int
    by_tier: list[GroupStats] = Field(default_factory=list)
    by_category: list[GroupStats] = Field(default_factory=list)
    overall: GroupStats | None = None
    markdown: str = ""
    html: str = ""


def load_results(out_dir: Path) -> list[BenchItemResult]:
    jl = out_dir / "results.jsonl"
    if jl.is_file():
        return list(latest(read_jsonl(jl, BenchItemResult)).values())
    js = out_dir / "results.json"
    if js.is_file():
        return [BenchItemResult.model_validate(r) for r in json.loads(js.read_text())]
    raise FileNotFoundError(f"no results.jsonl / results.json in {out_dir}")


def _stats(group: str, rs: list[BenchItemResult]) -> GroupStats:
    # A provider outage never tested the model, so it lands in NO rate — the same rule
    # bench/_compare_report.arm_stats has always applied ("an hour spent retrying a 503
    # is not model latency").  final_mean/pass_rate were already safe because an outage
    # has no score; $/run and min/run were not, and one 60-minute storm cell inflated
    # min/run 12.8x.  It is also not an `error`: that column must keep meaning "a real
    # crash", or a storm and a bug read the same in the report.
    ev = [r for r in rs if r.status != "infra_failed"]
    picked = [r.score_picked for r in ev if r.score_picked is not None]
    bases = [r.score_baseline for r in ev if r.score_baseline is not None]
    deltas = [r.score_picked - r.score_baseline for r in ev if r.score_picked is not None and r.score_baseline is not None]
    return GroupStats(
        group=group, n=len(rs), n_scored=len(picked), n_evaluated=len(ev), infra_failed=len(rs) - len(ev),
        baseline_mean=_mean(bases), picked_mean=_mean(picked),
        picked_median=round(statistics.median(picked), 4) if picked else None, delta_mean=_mean(deltas),
        cost_mean=round(statistics.fmean([r.cost_usd for r in ev]), 4) if ev else 0.0,
        minutes_mean=round(statistics.fmean([r.minutes for r in ev]), 2) if ev else 0.0,
        errors=sum(1 for r in ev if r.status in ("error", "failed") or r.errors),
    )


def _grouped(rs: list[BenchItemResult], key: str) -> list[GroupStats]:
    groups: dict[str, list[BenchItemResult]] = {}
    for r in rs:
        groups.setdefault(getattr(r, key) or "(none)", []).append(r)
    names = sorted(groups, key=lambda g: (TIER_ORDER.get(g, 99), g))
    return [_stats(g, groups[g]) for g in names]


def _md_table(title: str, stats: list[GroupStats]) -> str:
    lines = [f"### {title}", "",
             "| group | n | scored | baseline | picked | median | Δ | $/run | min/run | errors | outage |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in stats:
        lines.append(f"| {s.group} | {s.n} | {s.n_scored} | {_fmt(s.baseline_mean)} | {_fmt(s.picked_mean)} | "
                     f"{_fmt(s.picked_median)} | {_fmt(s.delta_mean, '+.3f')} | "
                     f"{s.cost_mean:.2f} | {s.minutes_mean:.1f} | {s.errors} | {s.infra_failed} |")
    return "\n".join(lines) + "\n"


def _entry_for(r: BenchItemResult) -> RunEntry:
    """Gallery card for one bench result: the run record when the workspace has one,
    else a card built from the result row alone (errors, never-started runs — a
    bare ``entry_for_dir`` would show those as pending/broken without the scores)."""
    ws_path = Path(r.workspace) if r.workspace else None
    if ws_path is not None and (ws_path / "record.json").is_file():
        try:
            entry = entry_from_record(r.tier, Workspace(ws_path), load_record(ws_path))
        except RecordError:
            entry = None
        if entry is not None:
            entry.slug = r.id
            entry.minutes = r.minutes or entry.minutes
            entry.error = entry.error or r.errors
            return entry
    return RunEntry(
        battery=r.tier, slug=r.id, path=r.workspace or "", state="ok", title=r.id,
        generator=r.generator, judge=r.judge, score=r.score_picked, baseline_score=r.score_baseline,
        picked_round=r.picked_round, rounds=r.rounds, cost_usd=r.cost_usd, minutes=r.minutes, status=r.status,
        error=r.errors,
    )


def build_report(out_dir: Path | str, *, title: str | None = None) -> BenchReport:
    out = Path(out_dir)
    results = sorted(load_results(out), key=lambda r: (TIER_ORDER.get(r.tier, 99), r.id))
    meta = json.loads((out / "battery.json").read_text()) if (out / "battery.json").is_file() else {}
    name = title or meta.get("battery", {}).get("name", out.name)
    rep = BenchReport(name=name, n=len(results), by_tier=_grouped(results, "tier"),
                      by_category=_grouped(results, "category"), overall=_stats("all", results))
    opts = meta.get("options", {})
    md = [f"# bench report — {name}", "", f"runs: {len(results)}  ·  generator: {opts.get('generator') or 'default'}  ·  "
          f"judge: {opts.get('judge') or 'default'}  ·  rounds ≤ {opts.get('rounds', '?')}", ""]
    md.append(_md_table("overall", [rep.overall] if rep.overall else []))
    md.append(_md_table("by tier", rep.by_tier))
    md.append(_md_table("by category", rep.by_category))
    md += ["### per prompt", "", "| id | tier | category | baseline | picked | round | rounds | $ | min | stop |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r.id} | {r.tier} | {r.category} | {_fmt(r.score_baseline)} | {_fmt(r.score_picked)} | "
                  f"{'-' if r.picked_round is None else f'r{r.picked_round:02d}'} | {r.rounds} | {r.cost_usd:.2f} | "
                  f"{r.minutes:.1f} | {r.status}{' ⚠' if r.errors else ''} |")
    rep.markdown = "\n".join(md) + "\n"
    (out / "report.md").write_text(rep.markdown)
    rep.html = _html_gallery(out, name, results, rep)
    (out / "report.html").write_text(rep.html)
    return rep


def _html_table(stats: list[GroupStats]) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(s.group)}</td><td>{s.n}</td><td>{_fmt(s.baseline_mean)}</td><td>{_fmt(s.picked_mean)}</td>"
        f"<td>{_fmt(s.delta_mean, '+.3f')}</td><td>{s.cost_mean:.2f}</td>"
        f"<td>{s.minutes_mean:.1f}</td><td>{s.errors}</td><td>{s.infra_failed}</td></tr>"
        for s in stats
    )
    return ("<table><tr><th>group</th><th>n</th><th>baseline</th><th>picked</th><th>Δ</th><th>$/run</th>"
            f"<th>min/run</th><th>errors</th><th>outage</th></tr>{rows}</table>")


def _html_gallery(out: Path, name: str, results: list[BenchItemResult], rep: BenchReport) -> str:
    style = "<style>table{border-collapse:collapse;margin:8px 0 12px}td,th{border:1px solid #444;padding:3px 10px;font-size:13px}</style>"
    tables = (style + "<h3>overall + by tier</h3>" + _html_table(([rep.overall] if rep.overall else []) + rep.by_tier)
              + "<h3>by category</h3>" + _html_table(rep.by_category))
    sections: dict[str, RootSection] = {}
    for r in results:  # one section per tier (results are already tier-sorted)
        sections.setdefault(r.tier, RootSection(label=r.tier, path=str(out / "runs"))).entries.append(_entry_for(r))
    index = GalleryIndex(sections=list(sections.values()), roots=[str(out)])
    return render_static(index, title=f"bench — {name}", embed=True, extra_html=tables)
