"""The index page: status rail, filters, per-battery sections, cards + table.

Rendered identically for the local server and the static build — the only
difference is the :class:`~codeverse3d.addons.gallery.urls.UrlMaker` it is handed.  The
initial filter is applied **server-side** (so ``curl '/?track=graphics'`` and a
no-JS browser both see the right runs and the right summary) and then re-applied
client-side on every keystroke without a reload.

The summary is a *breakdown*, not a scoreboard: four disjoint buckets that add up
to the number of runs on screen (``89 shown = 40 passed + 40 failed + 4 unjudged
+ 5 error``).  "pass 50% (40/80), not ok 5" against "89 runs" is arithmetic the
reader has to do — and cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.addons.gallery.cards import TABLE_HEAD, fmt, render_card, render_row
from codeverse3d.addons.gallery.index import build_index
from codeverse3d.addons.gallery.model import (
    FILTER_KEYS,
    VERDICT_META,
    VERDICTS,
    GalleryIndex,
    RunEntry,
    match,
    sort_entries,
    summarize,
)
from codeverse3d.addons.gallery.theme import INDEX_CSS, INDEX_JS, esc, footer, page_shell, top_bar
from codeverse3d.addons.gallery.urls import THUMB_PX, StaticUrls, UrlMaker
from codeverse3d.proc import write_text_atomic


# --------------------------------------------------------------------------- summary
def _verdict_chip(bucket: str, count: int, *, total: int) -> str:
    label, cls, why = VERDICT_META[bucket]
    pct = f"{round(100 * count / total)}%" if total else "0%"
    return (f"<button type='button' class='vchip {cls}' data-verdict='{bucket}' "
            f"aria-pressed='false' title='{esc(why)} — click to show only these'>"
            f"<b class='num' id='vc-{bucket}'>{count}</b> {esc(label)}"
            f"<span class='pct num' id='vp-{bucket}'>{pct}</span></button>")


def _summary_strip(entries: list[RunEntry], total: int) -> str:
    """One compact rail: how many runs, what happened to them, what they cost.

    Deliberately ~1/3 the height of the old 7-tile grid — on a 900 px laptop the
    first row of runs has to be visible without scrolling."""
    s = summarize(entries)
    segs = "".join(
        f"<span class='seg {VERDICT_META[b][1]}' id='vs-{b}' style='flex:{s.breakdown[b]}'"
        f" title='{s.breakdown[b]} {b}'></span>" for b in VERDICTS)
    chips = "".join(_verdict_chip(b, s.breakdown[b], total=s.n) for b in VERDICTS)
    per_pass = "—" if s.usd_per_pass is None else f"${s.usd_per_pass:.2f}"
    nums = (f"<span title='mean / median best score'>score <b class='num' id='s-score'>"
            f"{fmt(s.mean_score)} / {fmt(s.median_score)}</b></span>"
            f"<span>spend <b class='num' id='s-cost'>${s.total_usd:.2f}</b></span>"
            f"<span title='total spend divided by the number of passing runs'>per pass "
            f"<b class='num' id='s-perpass'>"
            f"{per_pass}</b></span>"
            f"<span>wall clock <b class='num' id='s-time'>"
            f"{(f'{s.minutes / 60:.1f} h' if s.minutes >= 90 else f'{round(s.minutes)} min')}</b></span>")
    shown = f"{s.n}" if s.n == total else f"{s.n} <span class='faint'>of {total}</span>"
    return (
        "<section class='summary' aria-label='status breakdown'>"
        f"<div class='sumn'><b class='num' id='s-n'>{shown}</b> runs shown"
        f"<span class='eq faint'>=</span></div>"
        f"<div class='vchips' id='vchips'>{chips}</div>"
        f"<div class='vbar' id='vbar' aria-hidden='true'>{segs}</div>"
        f"<div class='sumnums'>{nums}</div></section>"
    )


# --------------------------------------------------------------------------- controls
def _select(name: str, label: str, options: list[str], value: str, *, any_label: str = "all") -> str:
    opts = [f"<option value=''>{esc(any_label)}</option>"]
    opts += [f"<option value='{esc(o)}'{' selected' if o == value else ''}>{esc(o)}</option>"
             for o in options]
    return (f"<label class='fld' for='f-{name}'><span class='flab'>{esc(label)}</span>"
            f"<select id='f-{name}' name='{name}'>{''.join(opts)}</select></label>")


def _controls(index: GalleryIndex, flt: dict[str, str], sort: str) -> str:
    facets = index.facets()
    q = esc(flt.get("q", ""))
    fields = [
        f"<label class='fld grow' for='f-q'><span class='flab'>search</span>"
        f"<input type='search' id='f-q' name='q' value='{q}' "
        f"placeholder='prompt, slug, model…  (press /)' autocomplete='off'></label>",
        _select("track", "track", facets["track"], flt.get("track", "")),
        _select("lang", "language", facets["lang"], flt.get("lang", "")),
        _select("backend", "backend", facets["backend"], flt.get("backend", "")),
        _select("tier", "tier", facets["tier"], flt.get("tier", "")),
        _select("verdict", "verdict", list(VERDICTS), flt.get("verdict", ""), any_label="any"),
        _select("battery", "battery", facets["battery"], flt.get("battery", "")),
        _select("sort", "sort", ["score", "cost", "time", "complexity", "name"], sort, any_label="score"),
        "<label class='fld'><span class='flab'>&nbsp;</span>"
        "<button class='btn' id='f-reset' type='button'>reset</button></label>",
    ]
    active = sum(1 for k in FILTER_KEYS if flt.get(k))
    badge = (f"<span class='tag pill-accent{'' if active else ' is-hidden'}' id='f-count'>"
             f"{active}</span><span class='factive' id='f-active'></span>")
    return (f"<details class='filters' id='filters' open><summary>filters {badge}</summary>"
            f"<form class='controls' onsubmit='return false'>{''.join(fields)}</form></details>")


# --------------------------------------------------------------------------- bulk bar
def _bulk_bar() -> str:
    """Multi-select actions.  It is on screen from the start (greyed out) because a
    bulk action nobody can see is a bulk action nobody uses.

    Compare / export / copy paths only: this server has no mutation route at all,
    so a "re-run" button would be a lie."""
    return (
        "<div class='selbar empty' id='selbar' role='region' aria-label='bulk actions'>"
        "<span class='selcount'><b class='num' id='sel-n'>0</b> selected"
        "<span class='xs' id='sel-hint'> — tick a card or row</span></span>"
        "<a class='btn primary' id='sel-compare' href='/compare'>compare →</a>"
        "<a class='btn' id='sel-csv' href='/export.csv'>export csv</a>"
        "<button class='btn' type='button' id='sel-paths'>copy paths</button>"
        "<button class='btn' type='button' id='sel-all'>select all shown</button>"
        "<button class='btn' type='button' id='sel-clear'>clear</button>"
        "<span class='xs faint selnote'>read-only · no re-run from the browser</span></div>"
    )


# --------------------------------------------------------------------------- sections
def _section(label: str, path: str, entries: list[RunEntry], hidden: set[str], urls: UrlMaker) -> str:
    cards = "".join(render_card(e, urls, hidden=e.key in hidden) for e in entries)
    rows = "".join(render_row(e, urls, hidden=e.key in hidden) for e in entries)
    visible = sum(1 for e in entries if e.key not in hidden)
    return (
        f"<section class='battery{'' if visible else ' is-hidden'}' id='b-{esc(label)}'>"
        f"<h2>{esc(label)} <span class='count' data-count>{visible} run{'' if visible == 1 else 's'}</span>"
        f"<span class='rootpath'>{esc(path)}</span></h2>"
        f"<div class='grid' data-items>{cards}</div>"
        f"<div class='tablewrap'><div class='narrowhint xs faint'>narrow screen — "
        f"thumbnail, backend, baseline, Δ, tier, minutes and links are hidden — switch to cards or widen the window"
        f"</div>"
        f"<table><thead>{TABLE_HEAD}</thead>"
        f"<tbody data-items>{rows}</tbody></table></div></section>"
    )


def render_index(index: GalleryIndex, urls: UrlMaker, *, title: str = "3dcode gallery",
                 flt: dict[str, str] | None = None, sort: str = "score", view: str = "cards",
                 note: str = "", extra_html: str = "") -> str:
    """The whole index page as one HTML document.  ``extra_html`` (already
    escaped markup, e.g. eval/bench/report.py's stats tables) goes under the summary strip."""
    flt = {k: (flt or {}).get(k, "") for k in FILTER_KEYS}
    all_entries = index.entries()
    selected = [e for e in all_entries if match(e, flt)]
    hidden = {e.key for e in all_entries} - {e.key for e in selected}
    data = json.dumps([e.card_data() for e in all_entries], separators=(",", ":")).replace("</", "<\\/")
    sections = "".join(
        _section(s.label, s.path, sort_entries(s.entries, sort), hidden, urls)
        for s in index.sections if s.entries)
    if not sections:
        sections = "<div class='empty'>no runs found under the given roots</div>"
    empty = (f"<div class='empty{'' if not selected else ' is-hidden'}' id='empty'>"
             "nothing matches this filter</div>")
    right = (f"<button class='btn' id='view-btn' type='button' aria-pressed='{'true' if view == 'table' else 'false'}'>"
             f"{'▤ table' if view == 'table' else '▦ cards'}</button>")
    body = (
        top_bar("3dcode gallery", f"{len(all_entries)} runs · {len(index.sections)} roots", right=right)
        + "<main class='wrap'>"
        + (f"<p class='small muted'>{esc(note)}</p>" if note else "")
        + _summary_strip(selected, len(all_entries))
        + extra_html
        + _controls(index, flt, sort)
        + sections + empty + "</main>"
        + (_bulk_bar() if urls.has_detail else "")
        + footer(f"built in {index.build_ms} ms · {index.built_at:%Y-%m-%d %H:%M UTC} · "
                 f"roots: {', '.join(index.roots)}")
    )
    data_block = f"<script type='application/json' id='gallery-data'>{data}</script>"
    return page_shell(title, body + data_block, scripts=INDEX_JS, extra_css=INDEX_CSS,
                      body_class="view-table" if view == "table" else "")


# ===================================================================== static_site
def render_static(index: GalleryIndex, *, title: str = "3dcode gallery", embed: bool = False,
                  thumb_px: int = THUMB_PX, sort: str = "score", view: str = "cards",
                  extra_html: str = "") -> str:
    """The complete HTML document for ``index`` (no server involved)."""
    note = ("images are inlined; the links open the original run directories on this machine"
            if embed else "images and links point at the run directories with file:// — "
                          "use `3dcode gallery serve` for a page that works anywhere")
    return render_index(index, StaticUrls(embed=embed, thumb_px=thumb_px),
                        title=title, sort=sort, view=view, note=note, extra_html=extra_html)


def build_static(roots: list[Path] | list[str], out_html: Path | str, *, title: str | None = None,
                 embed: bool = False, thumb_px: int = THUMB_PX) -> tuple[Path, int, GalleryIndex]:
    """Scan ``roots`` and write one self-contained page; ``(path, n_runs, index)``."""
    index = build_index(roots)
    label = title or ("3dcode gallery — " + ", ".join(s.label for s in index.sections[:4])
                      + ("…" if len(index.sections) > 4 else ""))
    html = render_static(index, title=label, embed=embed, thumb_px=thumb_px)
    return write_text_atomic(Path(out_html), html), len(index.entries()), index
