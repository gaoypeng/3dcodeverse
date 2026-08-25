"""Aggregate a bench run: per-tier / per-category stats → markdown + HTML gallery.

``build_report(out_dir)`` reads ``results.jsonl`` (or results.json), aggregates
mean/median/pass-rate/cost per tier and category, writes ``report.md`` and
``report.html`` — the same self-contained gallery as ``3dcv flywheel gallery``
(``codeverse.flywheel.gallery``) with the stats tables above the cards.
"""

from __future__ import annotations

import html
import json
import statistics
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bench._jsonl import read_jsonl
from bench.run_bench import BenchItemResult
from codeverse.flywheel.gallery import GalleryItem, item_from_run, render_gallery
from codeverse.flywheel.record import RecordError, load_record
from codeverse.workspace import Workspace

TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2}


class GroupStats(BaseModel):
    group: str
    n: int
    n_scored: int
    baseline_mean: float | None
    final_mean: float | None
    final_median: float | None
    delta_mean: float | None
    pass_rate: float | None
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
        latest: dict[str, BenchItemResult] = {r.id: r for r in read_jsonl(jl, BenchItemResult)}
        return list(latest.values())
    js = out_dir / "results.json"
    if js.is_file():
        return [BenchItemResult.model_validate(r) for r in json.loads(js.read_text())]
    raise FileNotFoundError(f"no results.jsonl / results.json in {out_dir}")


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 4) if xs else None


def _stats(group: str, rs: list[BenchItemResult]) -> GroupStats:
    finals = [r.score_final for r in rs if r.score_final is not None]
    bases = [r.score_baseline for r in rs if r.score_baseline is not None]
    deltas = [r.score_final - r.score_baseline for r in rs if r.score_final is not None and r.score_baseline is not None]
    passed = [r.passed for r in rs if r.passed is not None]
    return GroupStats(
        group=group, n=len(rs), n_scored=len(finals), baseline_mean=_mean(bases), final_mean=_mean(finals),
        final_median=round(statistics.median(finals), 4) if finals else None, delta_mean=_mean(deltas),
        pass_rate=round(sum(passed) / len(passed), 4) if passed else None,
        cost_mean=round(statistics.fmean([r.cost_usd for r in rs]), 4) if rs else 0.0,
        minutes_mean=round(statistics.fmean([r.minutes for r in rs]), 2) if rs else 0.0,
        errors=sum(1 for r in rs if r.status in ("error", "failed") or r.errors),
    )


def _grouped(rs: list[BenchItemResult], key: str) -> list[GroupStats]:
    groups: dict[str, list[BenchItemResult]] = {}
    for r in rs:
        groups.setdefault(getattr(r, key) or "(none)", []).append(r)
    names = sorted(groups, key=lambda g: (TIER_ORDER.get(g, 99), g))
    return [_stats(g, groups[g]) for g in names]


def _fmt(v: float | None, spec: str = ".3f") -> str:
    return "-" if v is None else format(v, spec)


def _md_table(title: str, stats: list[GroupStats]) -> str:
    lines = [f"### {title}", "", "| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in stats:
        lines.append(f"| {s.group} | {s.n} | {s.n_scored} | {_fmt(s.baseline_mean)} | {_fmt(s.final_mean)} | "
                     f"{_fmt(s.final_median)} | {_fmt(s.delta_mean, '+.3f')} | {_fmt(s.pass_rate, '.0%')} | "
                     f"{s.cost_mean:.2f} | {s.minutes_mean:.1f} | {s.errors} |")
    return "\n".join(lines) + "\n"


def _item_for(r: BenchItemResult) -> GalleryItem:
    """Gallery card for one bench result: the run record when the workspace has one,
    else a card built from the result row alone (errors, never-started runs)."""
    ws_path = Path(r.workspace) if r.workspace else None
    if ws_path is not None and (ws_path / "record.json").is_file():
        try:
            item = item_from_run(Workspace(ws_path), load_record(ws_path))
        except RecordError:
            item = None
        if item is not None:
            item.key = r.id
            item.group = f"{r.tier} · {r.category}" if r.category else r.tier
            item.minutes = r.minutes or item.minutes
            item.error = item.error or r.errors
            return item
    return GalleryItem(
        key=r.id, title=r.id, group=f"{r.tier} · {r.category}" if r.category else r.tier,
        generator=r.generator, judge=r.judge, score=r.score_final, baseline_score=r.score_baseline,
        passed=r.passed, rounds=r.rounds, cost_usd=r.cost_usd, minutes=r.minutes, status=r.status,
        error=r.errors, links={"workspace": r.workspace} if r.workspace else {},
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
    md += ["### per prompt", "", "| id | tier | category | baseline | final | passed | rounds | $ | min | status |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r.id} | {r.tier} | {r.category} | {_fmt(r.score_baseline)} | {_fmt(r.score_final)} | "
                  f"{'-' if r.passed is None else ('yes' if r.passed else 'no')} | {r.rounds} | {r.cost_usd:.2f} | "
                  f"{r.minutes:.1f} | {r.status}{' ⚠' if r.errors else ''} |")
    rep.markdown = "\n".join(md) + "\n"
    (out / "report.md").write_text(rep.markdown)
    rep.html = _html_gallery(out, name, results, rep)
    (out / "report.html").write_text(rep.html)
    return rep


def _html_table(stats: list[GroupStats]) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(s.group)}</td><td>{s.n}</td><td>{_fmt(s.baseline_mean)}</td><td>{_fmt(s.final_mean)}</td>"
        f"<td>{_fmt(s.delta_mean, '+.3f')}</td><td>{_fmt(s.pass_rate, '.0%')}</td><td>{s.cost_mean:.2f}</td>"
        f"<td>{s.minutes_mean:.1f}</td><td>{s.errors}</td></tr>"
        for s in stats
    )
    return ("<table><tr><th>group</th><th>n</th><th>baseline</th><th>final</th><th>Δ</th><th>pass</th><th>$/run</th>"
            f"<th>min/run</th><th>errors</th></tr>{rows}</table>")


def _html_gallery(out: Path, name: str, results: list[BenchItemResult], rep: BenchReport) -> str:
    style = "<style>table{border-collapse:collapse;margin:8px 0 12px}td,th{border:1px solid #444;padding:3px 10px;font-size:13px}</style>"
    tables = (style + "<h3>overall + by tier</h3>" + _html_table(([rep.overall] if rep.overall else []) + rep.by_tier)
              + "<h3>by category</h3>" + _html_table(rep.by_category))
    items = [_item_for(r) for r in results]
    return render_gallery(items, f"bench — {name}", extra_html=tables)


def report_dict(rep: BenchReport) -> dict[str, Any]:
    return rep.model_dump(mode="json", exclude={"markdown", "html"})
