"""The index page: summary strip, filters, per-battery sections, cards + table.

Rendered identically for the local server and the static build — the only
difference is the :class:`~codeverse.gallery.urls.UrlMaker` it is handed.  The
initial filter is applied **server-side** (so ``curl '/?track=graphics'`` and a
no-JS browser both see the right runs and the right summary) and then re-applied
client-side on every keystroke without a reload.
"""

from __future__ import annotations

import json

from codeverse.gallery.model import (
    FILTER_KEYS,
    GalleryIndex,
    RunEntry,
    match,
    sort_entries,
    summarize,
)
from codeverse.gallery.scripts import INDEX_JS
from codeverse.gallery.theme import esc, footer, page_shell, top_bar
from codeverse.gallery.urls import UrlMaker

INDEX_CSS = """
.tablewrap{display:none}
body.view-table .grid{display:none}
body.view-table .tablewrap{display:block}
body.view-table section.battery > .rootpath{margin-bottom:var(--s-2)}
.card.broken .body{gap:8px}
"""


def _fmt(value: float | None, digits: int = 3, dash: str = "—") -> str:
    return dash if value is None else format(value, f".{digits}f")


def _tier_tag(entry: RunEntry) -> str:
    return f"<span class='tag tier {esc(entry.tier)}' title='quality tier'>{esc(entry.tier)}</span>"


def _pass_tag(entry: RunEntry) -> str:
    if entry.passed is None:
        return "<span class='tag' title='never judged'>unjudged</span>"
    cls = "pill-pass" if entry.passed else "pill-fail"
    return f"<span class='tag {cls}'>{'passed' if entry.passed else 'failed'}</span>"


def _links_html(entry: RunEntry, urls: UrlMaker) -> str:
    parts = []
    if urls.has_detail:
        parts.append(f"<a href='{esc(urls.detail(entry))}'><b>detail</b></a>")
    parts += [f"<a href='{esc(urls.link(entry, link))}'>{esc(link.label)}</a>" for link in entry.links]
    return "<div class='links'>" + "".join(parts) + "</div>"


def _shot(entry: RunEntry, urls: UrlMaker) -> str:
    if not entry.sheet:
        return "<div class='shot'><div class='noshot'>no render</div></div>"
    href = urls.file(entry, entry.sheet)
    alt = f"contact sheet — {entry.slug}"
    return (f"<a class='shot' href='{esc(href)}' title='open the full-size sheet'>"
            f"<img loading='lazy' decoding='async' src='{esc(urls.img(entry, entry.sheet))}' alt='{esc(alt)}'></a>")


def render_card(entry: RunEntry, urls: UrlMaker, *, hidden: bool = False) -> str:
    """One run card.  A pending / broken run keeps the same shape and says why.

    ``hidden`` pre-applies the server-side filter, so a curl'd query string shows
    the right runs while the client can still widen the filter without a reload."""
    hide = " is-hidden" if hidden else ""
    name = entry.title or entry.slug
    title_href = urls.detail(entry)
    head = (f"<div class='titlerow'>{_tier_tag(entry)}"
            f"<a class='name' href='{esc(title_href)}'>{esc(name)}</a>"
            f"<span class='score num' title='best score'>{_fmt(entry.score)}</span></div>")
    if entry.state != "ok":
        body = (f"{head}<div class='err'>{esc(entry.state)}: {esc(entry.error)}</div>"
                f"<p class='prompt'>{esc(entry.prompt)}</p>{_links_html(entry, urls)}")
        return (f"<article class='card broken na{hide}' data-run='{esc(entry.key)}'>"
                f"<div class='body'>{body}</div></article>")
    tags = "".join(f"<span class='tag'>{esc(t)}</span>" for t in (entry.track, entry.language, entry.generator) if t)
    delta = ""
    if entry.baseline_score is not None and entry.score is not None:
        delta = f" <span class='faint'>({entry.score - entry.baseline_score:+.3f})</span>"
    best = f"r{entry.best_round}" if entry.best_round is not None else "r–"
    facts2 = (f"<span>baseline {_fmt(entry.baseline_score)} → best {_fmt(entry.score)}{delta}</span>"
              f"<span>{best} of {entry.rounds} round{'' if entry.rounds == 1 else 's'}</span>")
    gates = (f"<span class='tag pill-warn'>{entry.gate_errors} gate err</span>" if entry.gate_errors
             else "<span class='tag'>gates clean</span>")
    money = (f"${entry.cost_usd:.2f}" + (f" · {entry.minutes:.1f} min" if entry.minutes is not None else ""))
    # the run status only earns a chip when it says something the verdict does not
    # ("passed" next to a green `passed` pill is noise; "plateau"/"budget"/"failed" is not)
    redundant = not entry.status or (entry.passed and entry.status == "passed")
    status = "" if redundant else f"<span class='tag'>{esc(entry.status)}</span>"
    caption = f"<p class='xs faint'>{esc(entry.caption)}</p>" if entry.caption else ""
    err = f"<div class='err'>{esc(entry.error[:300])}</div>" if entry.error else ""
    return (
        f"<article class='card {entry.pass_state}{hide}' data-run='{esc(entry.key)}'>"
        f"{_shot(entry, urls)}"
        f"<div class='body'>{head}"
        f"<p class='prompt' title='{esc(entry.prompt)}'>{esc(entry.prompt)}</p>{caption}"
        f"<div class='facts'>{tags}</div>"
        f"<div class='facts small'>{facts2}</div>"
        f"<div class='facts'>{_pass_tag(entry)}{gates}<span class='tag'>{esc(money)}</span>{status}</div>"
        f"{err}{_links_html(entry, urls)}</div></article>"
    )


TABLE_HEAD = ("<tr><th>run</th><th>track</th><th>lang</th><th>backend</th><th>tier</th>"
              "<th class='n'>base</th><th class='n'>best</th><th class='n'>Δ</th><th class='n'>gates</th>"
              "<th class='n'>$</th><th class='n'>min</th><th>status</th><th>links</th></tr>")


def render_row(entry: RunEntry, urls: UrlMaker, *, hidden: bool = False) -> str:
    delta = (entry.score - entry.baseline_score
             if entry.score is not None and entry.baseline_score is not None else None)
    links = " ".join(f"<a href='{esc(urls.link(entry, link))}'>{esc(link.label)}</a>"
                     for link in entry.links if link.label in ("workspace", "src/", "sheet", "glb"))
    return (
        f"<tr class='{'is-hidden' if hidden else ''}' data-run='{esc(entry.key)}'>"
        f"<td class='wide'><a href='{esc(urls.detail(entry))}'>{esc(entry.title or entry.slug)}</a>"
        f"<div class='xs faint'>{esc(entry.slug)}</div></td>"
        f"<td>{esc(entry.track)}</td><td>{esc(entry.language)}</td><td>{esc(entry.generator)}</td>"
        f"<td>{_tier_tag(entry)}</td>"
        f"<td class='n'>{_fmt(entry.baseline_score)}</td><td class='n'>{_fmt(entry.score)}</td>"
        f"<td class='n'>{'—' if delta is None else format(delta, '+.3f')}</td>"
        f"<td class='n'>{entry.gate_errors}</td><td class='n'>{entry.cost_usd:.2f}</td>"
        f"<td class='n'>{_fmt(entry.minutes, 1)}</td>"
        f"<td>{esc(entry.status or entry.state)}</td><td>{links}</td></tr>"
    )


def _select(name: str, label: str, options: list[str], value: str, *, any_label: str = "all") -> str:
    opts = [f"<option value=''>{esc(any_label)}</option>"]
    opts += [f"<option value='{esc(o)}'{' selected' if o == value else ''}>{esc(o)}</option>" for o in options]
    return (f"<label for='f-{name}'>{esc(label)}"
            f"<select id='f-{name}' name='{name}'>{''.join(opts)}</select></label>")


def _controls(index: GalleryIndex, flt: dict[str, str], sort: str) -> str:
    facets = index.facets()
    q = esc(flt.get("q", ""))
    parts = [
        f"<label for='f-q'>search<input type='search' id='f-q' name='q' value='{q}' "
        f"placeholder='prompt, slug, model…  (/)' autocomplete='off'></label>",
        _select("track", "track", facets["track"], flt.get("track", "")),
        _select("lang", "language", facets["lang"], flt.get("lang", "")),
        _select("tier", "tier", facets["tier"], flt.get("tier", "")),
        _select("backend", "backend", facets["backend"], flt.get("backend", "")),
        _select("pass", "verdict", ["pass", "fail", "na"], flt.get("pass", ""), any_label="any"),
        _select("battery", "battery", facets["battery"], flt.get("battery", "")),
        _select("sort", "sort", ["score", "cost", "time", "name"], sort, any_label="score"),
        "<button class='btn' id='f-reset' type='button'>reset</button>",
    ]
    return f"<form class='controls' onsubmit='return false'>{''.join(parts)}</form>"


STATS = (("s-n", "runs"), ("s-pass", "pass rate"), ("s-score", "mean / median"), ("s-cost", "total $"),
         ("s-perpass", "$ per pass"), ("s-time", "wall clock"), ("s-broken", "not ok"))


def _summary_strip(entries: list[RunEntry], total: int) -> str:
    s = summarize(entries)
    values = {
        "s-n": str(s.n) if s.n == total else f"{s.n} / {total}",
        "s-pass": f"{round(100 * s.pass_rate)}%  ({s.n_passed}/{s.n_judged})" if s.pass_rate is not None else "—",
        "s-score": f"{_fmt(s.mean_score)}  /  {_fmt(s.median_score)}",
        "s-cost": f"${s.total_usd:.2f}",
        "s-perpass": f"${s.usd_per_pass:.2f}" if s.usd_per_pass is not None else "—",
        "s-time": f"{s.minutes / 60:.1f} h" if s.minutes >= 90 else f"{round(s.minutes)} min",
        "s-broken": str(s.broken),
    }
    cells = "".join(f"<div class='stat'><div class='k'>{esc(label)}</div>"
                    f"<div class='v num' id='{sid}'>{esc(values[sid])}</div></div>" for sid, label in STATS)
    return f"<div class='strip'>{cells}</div>"


def _section(label: str, path: str, entries: list[RunEntry], hidden: set[str], urls: UrlMaker) -> str:
    cards = "".join(render_card(e, urls, hidden=e.key in hidden) for e in entries)
    rows = "".join(render_row(e, urls, hidden=e.key in hidden) for e in entries)
    visible = sum(1 for e in entries if e.key not in hidden)
    return (
        f"<section class='battery{'' if visible else ' is-hidden'}' id='b-{esc(label)}'>"
        f"<h2>{esc(label)} <span class='count' data-count>{visible} run{'' if visible == 1 else 's'}</span></h2>"
        f"<div class='rootpath'>{esc(path)}</div>"
        f"<div class='grid' data-items>{cards}</div>"
        f"<div class='tablewrap'><table><thead>{TABLE_HEAD}</thead>"
        f"<tbody data-items>{rows}</tbody></table></div></section>"
    )


def render_index(index: GalleryIndex, urls: UrlMaker, *, title: str = "3dcv gallery",
                 flt: dict[str, str] | None = None, sort: str = "score", view: str = "cards",
                 note: str = "") -> str:
    """The whole index page as one HTML document."""
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
    empty = ("<div class='empty is-hidden' id='empty'>nothing matches this filter</div>"
             if selected else "<div class='empty' id='empty'>nothing matches this filter</div>")
    right = (f"<button class='btn' id='view-btn' type='button' aria-pressed='{'true' if view == 'table' else 'false'}'>"
             f"{'▤ table' if view == 'table' else '▦ cards'}</button>")
    body = (
        top_bar("3dcv gallery", f"{len(all_entries)} runs · {len(index.sections)} roots", right=right)
        + "<main class='wrap'>"
        + (f"<p class='small muted'>{esc(note)}</p>" if note else "")
        + _summary_strip(selected, len(all_entries))
        + _controls(index, flt, sort)
        + sections + empty + "</main>"
        + footer(f"built in {index.build_ms} ms · {index.built_at:%Y-%m-%d %H:%M UTC} · "
                 f"roots: {', '.join(index.roots)}")
    )
    data_block = f"<script type='application/json' id='gallery-data'>{data}</script>"
    return page_shell(title, body + data_block, scripts=INDEX_JS, extra_css=INDEX_CSS,
                      body_class="view-table" if view == "table" else "")
