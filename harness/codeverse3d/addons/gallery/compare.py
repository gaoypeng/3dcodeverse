"""Bulk triage: the side-by-side compare page and the CSV export.

Both are **read-only projections of the index** — ``/compare?runs=a/b,c/d`` and
``/export.csv?runs=…`` (or any index filter).  Nothing here writes, queues or
re-runs anything: the gallery deliberately exposes no mutation endpoint, so the
bulk bar offers *compare*, *export* and *copy paths* and stops there.

The compare page is a metric-per-row table so the eye travels along one row and
compares like with like; a per-run column of stacked cards does not do that.
"""

from __future__ import annotations

import csv
import io
from urllib.parse import quote

from codeverse3d.addons.gallery.cards import fmt, tier_tag, verdict_tag
from codeverse3d.addons.gallery.model import VERDICT_META, RunEntry
from codeverse3d.addons.gallery.theme import esc, footer, page_shell, top_bar
from codeverse3d.addons.gallery.urls import UrlMaker

#: how many runs a compare page will lay out before it says "too many"
MAX_COMPARE = 8

COMPARE_CSS = """
.cmpwrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--r-2);background:var(--surface)}
table.cmp{border-collapse:collapse;width:max-content;min-width:100%;font-size:var(--fs-sm)}
table.cmp th.rowk{text-align:left;width:132px;min-width:132px;color:var(--fg-3);font-weight:600;
  position:sticky;left:0;background:var(--surface);z-index:1;text-transform:none;letter-spacing:0}
table.cmp td{vertical-align:top;min-width:200px;max-width:320px;white-space:normal;
  border-left:1px solid var(--line);overflow-wrap:anywhere}
table.cmp td code{word-break:break-all}
table.cmp tr:nth-child(even){background:color-mix(in srgb,var(--sunken) 55%,transparent)}
table.cmp tr:nth-child(even) th.rowk{background:color-mix(in srgb,var(--surface) 60%,var(--sunken))}
table.cmp img.cmpshot{width:100%;max-width:300px;border-radius:var(--r-2);background:var(--sunken);
  display:block;border:1px solid var(--line)}
table.cmp td.head{font-weight:650;font-size:var(--fs-md)}
.cmp .spark{display:flex;gap:3px;align-items:flex-end;height:26px}
.cmp .spark i{display:block;width:9px;background:var(--accent-soft);border-radius:2px 2px 0 0}
.cmp .spark i.best{background:var(--accent)}
table.cmp td a{margin-right:2px}
@media (max-width:640px){
  table.cmp th.rowk{width:80px;min-width:80px;font-size:var(--fs-xs);padding:7px 6px}
  table.cmp td{min-width:172px;max-width:210px;font-size:var(--fs-xs);padding:7px 8px}
  table.cmp img.cmpshot{max-width:156px}
  table.cmp td.head{font-size:var(--fs-sm)}
}
.swipehint{display:none}
@media (max-width:640px){.swipehint{display:block;margin-bottom:6px}}
"""


def parse_keys(value: str) -> list[str]:
    """``"a/b, c/d"`` → ``["a/b", "c/d"]`` (order kept, duplicates dropped)."""
    out: list[str] = []
    for chunk in (value or "").replace("\n", ",").split(","):
        key = chunk.strip()
        if key and key not in out:
            out.append(key)
    return out


def _spark(entry: RunEntry) -> str:
    rows = [r for r in entry.round_rows if r.score is not None]
    if len(rows) < 2:
        return "—"
    top = max(r.score or 0.0 for r in rows) or 1.0
    bars = "".join(
        f"<i style='height:{max(3, round(26 * (r.score or 0.0) / top))}px'"
        f"{' class=best' if r.index == entry.picked_round else ''}"
        f" title='r{r.index} {r.score:.3f}'></i>" for r in rows)
    return f"<div class='spark'>{bars}</div>"


def _row(label: str, entries: list[RunEntry], render, *, cls: str = "") -> str:
    attr = f" class='{cls}'" if cls else ""
    body = "".join(f"<td{attr}>{render(e)}</td>" for e in entries)
    return f"<tr><th class='rowk'>{esc(label)}</th>{body}</tr>"


def _delta(entry: RunEntry) -> str:
    if entry.score is None or entry.baseline_score is None:
        return "—"
    d = entry.score - entry.baseline_score
    return f"<span class='{'up' if d >= 0 else 'down'}'>{d:+.3f}</span>"


def render_compare(entries: list[RunEntry], urls: UrlMaker, *, note: str = "") -> str:
    """``n`` runs as columns, one metric per row."""
    if not entries:
        inner = ("<div class='panel'><h2>nothing to compare</h2><p class='muted'>Tick two or more "
                 "runs on the index and press <b>compare</b>.</p></div>")
        body = (top_bar("3dcode gallery", crumbs="<a href='/'>gallery</a> <span class='faint'>/</span> "
                        "<b>compare</b>") + f"<main class='wrap'>{inner}</main>" + footer("3dcode gallery"))
        return page_shell("compare — 3dcode gallery", body, extra_css=COMPARE_CSS)

    def shot(e: RunEntry) -> str:
        image = e.card_image
        if not image:
            return "<span class='faint'>no render</span>"
        return (f"<a href='{esc(urls.detail(e))}'><img class='cmpshot' loading='lazy' "
                f"src='{esc(urls.img(e, image))}' alt='{esc(e.slug)}'></a>")

    def head(e: RunEntry) -> str:
        return (f"<a href='{esc(urls.detail(e))}'>{esc(e.title or e.slug)}</a>"
                f"<div class='xs faint'>{esc(e.battery)} / {esc(e.slug)}</div>")

    def links(e: RunEntry) -> str:
        keep = ("src/", "sheet", "glb", "record.json")
        return " · ".join(f"<a href='{esc(urls.link(e, ln))}'>{esc(ln.label)}</a>"
                          for ln in e.links if ln.label in keep) or "—"

    rows = "".join([
        _row("run", entries, head, cls="head"),
        _row("render", entries, shot),
        _row("verdict", entries, lambda e: verdict_tag(e) + " " + tier_tag(e)),
        _row("score", entries, lambda e: f"<b class='num'>{fmt(e.score)}</b>"),
        _row("baseline", entries, lambda e: f"<span class='num'>{fmt(e.baseline_score)}</span>"),
        _row("Δ vs baseline", entries, _delta),
        _row("per round", entries, _spark),
        _row("gate errors", entries, lambda e: str(e.gate_errors) + (
            " <span class='xs faint'>" + esc(", ".join(f"{k}:{v}" for k, v in e.gate_summary.items()))
            + "</span>" if e.gate_summary else "")),
        _row("rounds", entries, lambda e: f"{e.rounds} · picked r{e.picked_round if e.picked_round is not None else '–'}"),
        _row("cost", entries, lambda e: f"${e.cost_usd:.3f}"),
        _row("minutes", entries, lambda e: "—" if e.minutes is None else f"{e.minutes:.1f}"),
        _row("track", entries, lambda e: esc(e.track)),
        _row("language", entries, lambda e: esc(e.language)),
        _row("generator", entries, lambda e: esc(e.generator)),
        _row("judge", entries, lambda e: esc(e.judge)),
        _row("prompt", entries, lambda e: f"<span class='small'>{esc(e.prompt)}</span>"),
        _row("workspace", entries, lambda e: f"<code class='xs'>{esc(e.path)}</code>"),
        _row("links", entries, links),
    ])
    counts = {k: sum(1 for e in entries if e.verdict == k) for k, _ in VERDICT_META.items()}
    tally = " · ".join(f"{v} {k}" for k, v in counts.items() if v)
    body = (
        top_bar("3dcode gallery", f"{len(entries)} runs · {tally}",
                crumbs="<a href='/'>gallery</a> <span class='faint'>/</span> <b>compare</b>",
                right=f"<a class='btn' href='/export.csv?runs={quote(','.join(e.key for e in entries))}'>"
                      f"export csv</a>")
        + "<main class='wrap'>"
        + (f"<p class='small muted'>{esc(note)}</p>" if note else "")
        + "<p class='swipehint xs faint'>swipe sideways to reach the other runs — "
              "the metric names stay pinned on the left</p>"
            + f"<div class='cmpwrap cmp'><table class='cmp'><tbody>{rows}</tbody></table></div>"
        + "<p class='small faint' style='margin-top:var(--s-3)'>read-only view — the gallery never "
          "starts, resumes or deletes a run.</p>"
        + "</main>" + footer("compare · 3dcode gallery"))
    return page_shell("compare — 3dcode gallery", body, extra_css=COMPARE_CSS)


#: a leading one of these makes a spreadsheet treat the cell as a formula (CSV injection)
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def csv_safe(value: object) -> object:
    """Neutralise spreadsheet formula injection: a string cell starting with
    ``= + - @`` (an LLM-written prompt or title can) is prefixed with ``'``."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


CSV_COLUMNS = ("battery", "slug", "title", "verdict", "tier", "track", "language", "generator",
               "judge", "score", "baseline_score", "delta", "gate_errors", "cost_usd", "minutes",
               "rounds", "picked_round", "status", "state", "path", "prompt")


def export_csv(entries: list[RunEntry]) -> str:
    """The selected runs as CSV — the one export a read-only tool owes a spreadsheet."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for e in entries:
        delta = (e.score - e.baseline_score
                 if e.score is not None and e.baseline_score is not None else None)
        writer.writerow([csv_safe(v) for v in [
            e.battery, e.slug, e.title, e.verdict, e.tier, e.track, e.language, e.generator,
            e.judge, "" if e.score is None else f"{e.score:.4f}",
            "" if e.baseline_score is None else f"{e.baseline_score:.4f}",
            "" if delta is None else f"{delta:+.4f}", e.gate_errors, f"{e.cost_usd:.4f}",
            "" if e.minutes is None else f"{e.minutes:.2f}", e.rounds,
            "" if e.picked_round is None else e.picked_round, e.status, e.state, e.path, e.prompt,
        ]])
    return buf.getvalue()
