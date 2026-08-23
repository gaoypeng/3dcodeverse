"""Aggregate a bench run: per-tier / per-category stats → markdown + HTML gallery.

``build_report(out_dir)`` reads ``results.jsonl`` (or results.json), aggregates
mean/median/pass-rate/cost per tier and category, writes ``report.md`` and
``report.html`` (contact sheets copied into ``report_assets/``).
"""

from __future__ import annotations

import html
import json
import shutil
import statistics
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bench.run_bench import BenchItemResult

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
        latest: dict[str, BenchItemResult] = {}
        for line in jl.read_text().splitlines():
            if line.strip():
                r = BenchItemResult.model_validate_json(line)
                latest[r.id] = r
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


def _sheet_for(r: BenchItemResult) -> Path | None:
    ws = Path(r.workspace) if r.workspace else None
    if ws is None or not (ws / "record.json").is_file():
        return None
    try:
        rec = json.loads((ws / "record.json").read_text())
    except ValueError:
        return None
    best = rec.get("best_round")
    for rnd in rec.get("rounds", []):
        if rnd.get("index") == best and rnd.get("renders") and rnd["renders"].get("contact_sheet"):
            p = Path(rnd["renders"]["contact_sheet"])
            p = p if p.is_absolute() else ws / p
            return p if p.is_file() else None
    return None


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


def _html_gallery(out: Path, name: str, results: list[BenchItemResult], rep: BenchReport) -> str:
    assets = out / "report_assets"
    assets.mkdir(exist_ok=True)
    cards = []
    for r in results:
        sheet = _sheet_for(r)
        img = ""
        if sheet is not None:
            dst = assets / f"{r.id}{sheet.suffix}"
            shutil.copy2(sheet, dst)
            img = f'<img src="report_assets/{dst.name}" loading="lazy">'
        badge = "pass" if r.passed else ("fail" if r.passed is False else "na")
        cards.append(
            f'<div class="card {badge}">{img}<div class="meta"><b>{html.escape(r.id)}</b> · {html.escape(r.tier)} · '
            f'{html.escape(r.category)}<br>baseline {_fmt(r.score_baseline)} → final {_fmt(r.score_final)} · '
            f'rounds {r.rounds} · ${r.cost_usd:.2f} · {r.minutes:.1f} min · {html.escape(r.status)}'
            f'{"<br><span class=err>" + html.escape(r.errors[:200]) + "</span>" if r.errors else ""}</div></div>'
        )

    def _rows(stats: list[GroupStats]) -> str:
        return "".join(
            f"<tr><td>{html.escape(s.group)}</td><td>{s.n}</td><td>{_fmt(s.baseline_mean)}</td><td>{_fmt(s.final_mean)}</td>"
            f"<td>{_fmt(s.delta_mean, '+.3f')}</td><td>{_fmt(s.pass_rate, '.0%')}</td><td>{s.cost_mean:.2f}</td></tr>"
            for s in stats
        )

    table = ("<table><tr><th>group</th><th>n</th><th>baseline</th><th>final</th><th>Δ</th><th>pass</th><th>$/run</th></tr>"
             + _rows([rep.overall] if rep.overall else []) + _rows(rep.by_tier) + "</table>")
    style = ("body{font-family:system-ui,sans-serif;margin:24px;background:#111;color:#eee}"
             ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:14px}"
             ".card{background:#1c1c1c;border-radius:8px;overflow:hidden;border:2px solid #333}.card.pass{border-color:#2e7d32}"
             ".card.fail{border-color:#8a1c1c}.card img{width:100%;display:block}.meta{padding:8px;font-size:13px}"
             ".err{color:#f88}table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #444;padding:4px 10px}")
    return (f"<!doctype html><meta charset='utf-8'><title>bench {html.escape(name)}</title><style>{style}</style>"
            f"<h1>bench — {html.escape(name)}</h1>{table}<div class='grid'>{''.join(cards)}</div>")


def report_dict(rep: BenchReport) -> dict[str, Any]:
    return rep.model_dump(mode="json", exclude={"markdown", "html"})
