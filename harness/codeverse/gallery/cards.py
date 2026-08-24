"""One run, drawn three ways: a card, a table row, and a compare column.

Design rules this module exists to hold in one place:

* **one hero image per card.**  A card is a scanning target, not a proof sheet:
  eight 70 px tiles in a 320 px card are eight illegible tiles.  The contact
  sheet is still one click away (the ``⊞ n`` disclosure over the image).
* **two links, then a menu.**  ``detail`` plus the one artifact that track
  actually wants; everything else lives behind ``⋮`` so a wall of blue text
  never competes with the render.
* **the verdict is peripheral.**  A 6 px coloured rail down the left edge is
  readable out of the corner of the eye while scrolling; a pill is not.
"""

from __future__ import annotations

from codeverse.gallery.labels import VERDICT_META, humanize_view
from codeverse.gallery.model import RunEntry, RunLink
from codeverse.gallery.theme import esc
from codeverse.gallery.urls import UrlMaker

#: links promoted next to ``detail`` on the card, best-first — the one artifact
#: you actually open for that track
PRIMARY_LINKS = ("glb", "textured glb", "preview.gif", "articulation", "src/")


def fmt(value: float | None, digits: int = 3, dash: str = "—") -> str:
    return dash if value is None else format(value, f".{digits}f")


def complexity_chip(entry: RunEntry) -> str:
    """``cx 0.65`` — how much artifact was actually built, next to what it scored.

    Complexity is *difficulty*, not quality: the pair is the point.  A run with no
    complexity block (a graphics/scene run, or one recorded before the vector
    existed) simply shows nothing."""
    if entry.complexity is None:
        return ""
    axes = " · ".join(f"{k.replace('_', ' ')} {v:g}" for k, v in entry.complexity_axes.items())
    title = f"objective complexity index {entry.complexity:.3f} ({entry.complexity_band}) — {axes}"
    return f"<span class='num' title='{esc(title)}'>cx {entry.complexity:.2f}</span>"


def tier_tag(entry: RunEntry) -> str:
    return f"<span class='tag tier {esc(entry.tier)}' title='quality tier {esc(entry.tier)}'>{esc(entry.tier)}</span>"


def verdict_tag(entry: RunEntry) -> str:
    label, cls, why = VERDICT_META[entry.verdict]
    return f"<span class='tag {cls}' title='{esc(why)}'>{esc(label)}</span>"


def _menu(entry: RunEntry, urls: UrlMaker, shown: set[str]) -> str:
    """Everything that did not earn a place on the card, behind a ``⋮``."""
    rest = [ln for ln in entry.links if ln.label not in shown]
    if not rest:
        return ""
    items = "".join(f"<a href='{esc(urls.link(entry, ln))}'>{esc(ln.label)}</a>" for ln in rest)
    return ("<details class='menu'><summary title='every other artifact of this run'"
            f" aria-label='more links'>⋮</summary><div class='pop'>{items}</div></details>")


def _primary(entry: RunEntry) -> RunLink | None:
    by_label = {ln.label: ln for ln in entry.links}
    return next((by_label[name] for name in PRIMARY_LINKS if name in by_label), None)


def card_actions(entry: RunEntry, urls: UrlMaker) -> str:
    shown = {"detail"}
    parts = []
    if urls.has_detail:
        parts.append(f"<a class='go' href='{esc(urls.detail(entry))}'>detail →</a>")
    primary = _primary(entry)
    if primary is not None:
        shown.add(primary.label)
        parts.append(f"<a href='{esc(urls.link(entry, primary))}'>{esc(primary.label)}</a>")
    if not urls.has_detail:  # the static build has no detail page: offer the workspace instead
        parts.insert(0, f"<a class='go' href='{esc(urls.dir(entry))}'>workspace →</a>")
        shown.add("workspace")
    return f"<div class='actions'>{''.join(parts)}{_menu(entry, urls, shown)}</div>"


def card_shot(entry: RunEntry, urls: UrlMaker) -> str:
    """The hero view, with the full contact sheet one disclosure click away."""
    image = entry.card_image
    if not image:
        return "<div class='shot'><div class='noshot'>no render</div></div>"
    label = entry.hero_label or "contact sheet"
    alt = f"{label} — {entry.slug}"
    inner = (f"<a href='{esc(urls.detail(entry) if urls.has_detail else urls.file(entry, image))}'>"
             f"<img loading='lazy' decoding='async' src='{esc(urls.img(entry, image))}' "
             f"alt='{esc(alt)}'></a><span class='viewtag'>{esc(label)}</span>")
    if entry.sheet and entry.hero and entry.n_views > 1:
        inner += (f"<details class='views'><summary title='all {entry.n_views} views'>"
                  f"⊞ {entry.n_views}</summary>"
                  f"<img class='sheetover' loading='lazy' decoding='async' "
                  f"src='{esc(urls.img(entry, entry.sheet))}' alt='all views — {esc(entry.slug)}'>"
                  f"</details>")
    return f"<div class='shot'>{inner}</div>"


def select_box(entry: RunEntry) -> str:
    return (f"<label class='pick' title='select for compare / export'>"
            f"<input type='checkbox' class='sel' data-key='{esc(entry.key)}'"
            f" aria-label='select {esc(entry.slug)}'></label>")


def render_card(entry: RunEntry, urls: UrlMaker, *, hidden: bool = False) -> str:
    """One run card.  A pending / broken run keeps the same shape and says why.

    ``hidden`` pre-applies the server-side filter, so a curl'd query string shows
    the right runs while the client can still widen the filter without a reload."""
    hide = " is-hidden" if hidden else ""
    name = entry.title or entry.slug
    head = (f"<div class='titlerow'>{tier_tag(entry)}"
            f"<a class='name' href='{esc(urls.detail(entry))}' title='{esc(entry.slug)}'>{esc(name)}</a>"
            f"<span class='score num' title='best score'>{fmt(entry.score)}</span></div>")
    prompt = (f"<p class='prompt' title='{esc(entry.prompt)}'>{esc(entry.prompt)}</p>"
              if entry.prompt else "")
    if entry.state != "ok":
        return (f"<article class='card broken v-err{hide}' data-run='{esc(entry.key)}'"
                f" data-verdict='error'>{select_box(entry)}<div class='body'>{head}"
                f"<div class='err'>{esc(entry.state)}: {esc(entry.error)}</div>{prompt}"
                f"{card_actions(entry, urls)}</div></article>")
    tags = "".join(f"<span class='tag'>{esc(t)}</span>"
                   for t in (entry.track, entry.language, entry.generator) if t)
    delta = ""
    if entry.baseline_score is not None and entry.score is not None:
        cls = "up" if entry.score >= entry.baseline_score else "down"
        delta = f" <span class='{cls}'>{entry.score - entry.baseline_score:+.3f}</span>"
    best = f"r{entry.best_round}" if entry.best_round is not None else "r–"
    cx = complexity_chip(entry)
    facts2 = (f"<span>{fmt(entry.baseline_score)} → <b>{fmt(entry.score)}</b>{delta}</span>"
              f"<span>{best}/{entry.rounds}</span>"
              f"<span>${entry.cost_usd:.2f}</span>"
              + (f"<span>{entry.minutes:.0f} min</span>" if entry.minutes is not None else "")
              + cx)
    gates = (f"<span class='tag pill-warn'>{entry.gate_errors} gate err</span>" if entry.gate_errors
             else "<span class='tag pill-ok'>gates clean</span>")
    # the run status only earns a chip when it says something the verdict does not
    # ("passed" next to a green `passed` pill is noise; "plateau"/"budget"/"failed" is not)
    redundant = not entry.status or (entry.passed and entry.status == "passed")
    status = "" if redundant else f"<span class='tag'>{esc(entry.status)}</span>"
    caption = f"<p class='xs faint clamp1'>{esc(entry.caption)}</p>" if entry.caption else ""
    err = f"<div class='err clamp2' title='{esc(entry.error)}'>{esc(entry.error[:300])}</div>" \
        if entry.error else ""
    return (
        f"<article class='card {VERDICT_META[entry.verdict][1]}{hide}' data-run='{esc(entry.key)}'"
        f" data-verdict='{esc(entry.verdict)}'>{select_box(entry)}{card_shot(entry, urls)}"
        f"<div class='body'>{head}{prompt}{caption}"
        f"<div class='facts'>{verdict_tag(entry)}{gates}{status}</div>"
        f"<div class='facts small num'>{facts2}</div>"
        f"<div class='facts tags'>{tags}</div>"
        f"{err}{card_actions(entry, urls)}</div></article>"
    )


TABLE_HEAD = ("<tr><th class='pickcol'><span class='sr'>select</span></th><th class='thumbcol'></th>"
              "<th class='runcol'>run</th><th>verdict</th><th class='track'>track</th>"
              "<th class='lang'>lang</th><th class='backend'>backend</th><th class='tier'>tier</th>"
              "<th class='n base'>base</th><th class='n'>best</th><th class='n delta'>Δ</th>"
              "<th class='n'>gates</th><th class='n'>$</th><th class='n min'>min</th>"
              "<th class='rowlinks'>links</th></tr>")


def render_row(entry: RunEntry, urls: UrlMaker, *, hidden: bool = False) -> str:
    delta = (entry.score - entry.baseline_score
             if entry.score is not None and entry.baseline_score is not None else None)
    links = " ".join(f"<a href='{esc(urls.link(entry, link))}'>{esc(link.label)}</a>"
                     for link in entry.links if link.label in ("src/", "glb"))
    image = entry.card_image
    thumb = (f"<a href='{esc(urls.detail(entry))}'><img class='rowthumb' loading='lazy' decoding='async'"
             f" src='{esc(urls.img(entry, image))}' alt=''></a>" if image else "")
    return (
        f"<tr class='{'is-hidden ' if hidden else ''}{VERDICT_META[entry.verdict][1]}'"
        f" data-run='{esc(entry.key)}' data-verdict='{esc(entry.verdict)}'>"
        f"<td class='pickcol'>{select_box(entry)}</td><td class='thumbcol'>{thumb}</td>"
        f"<td class='wide'><a href='{esc(urls.detail(entry))}'>{esc(entry.title or entry.slug)}</a>"
        f"<div class='xs faint'>{esc(entry.slug)}</div></td>"
        f"<td>{verdict_tag(entry)}</td>"
        f"<td class='track'>{esc(entry.track)}</td><td class='lang'>{esc(entry.language)}</td>"
        f"<td class='backend' title='{esc(entry.generator)}'>{esc(entry.generator)}</td>"
        f"<td class='tier'>{tier_tag(entry)}</td>"
        f"<td class='n base'>{fmt(entry.baseline_score)}</td><td class='n'>{fmt(entry.score)}</td>"
        f"<td class='n delta'>{'—' if delta is None else format(delta, '+.3f')}</td>"
        f"<td class='n'>{entry.gate_errors}</td><td class='n'>{entry.cost_usd:.2f}</td>"
        f"<td class='n min'>{fmt(entry.minutes, 1)}</td>"
        f"<td class='rowlinks'>{links}</td></tr>"
    )


def gallery_figure(label: str, href: str, src: str, *, eager: bool = False) -> str:
    """A render tile whose caption floats on a gradient instead of a black bar."""
    human = humanize_view(label)
    loading = "eager" if eager else "lazy"
    return (f"<figure><a href='{esc(href)}'>"
            f"<img loading='{loading}' decoding='async' src='{esc(src)}' alt='{esc(human)}'>"
            f"<figcaption>{esc(human)}</figcaption></a></figure>")
