"""The per-run detail page (``/run/<battery>/<slug>``).

Everything the harness recorded about one run, in the order you actually ask
for it: what it produced, how each round went, what the judge said about the
picked round (``addons/select``), what the deterministic measurement says, every
render, where the money went, and the code itself.  The record is re-read per request, so a run a
bench is still writing shows its latest rounds without a restart.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from codeverse3d.addons.gallery.cards import fmt, gallery_figure, tier_tag, verdict_tag
from codeverse3d.addons.gallery.code import CODE_CSS, numbered, read_text, src_files
from codeverse3d.addons.gallery.index import _rel
from codeverse3d.addons.gallery.model import RunEntry
from codeverse3d.addons.gallery.theme import esc, footer, page_shell, top_bar
from codeverse3d.addons.gallery.urls import UrlMaker
from codeverse3d.contracts.common import ENTRY_FILE
from codeverse3d.contracts.run import RoundRecord, RunRecord
from codeverse3d.record.record import effective_judgment
from codeverse3d.workspace import Workspace

DETAIL_CSS = CODE_CSS + """
.hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);gap:var(--s-4);align-items:start}
@media (max-width:900px){.hero{grid-template-columns:1fr}}
.hero .shotbox{position:relative;background-color:var(--shot-bg);
  background-image:repeating-conic-gradient(var(--shot-check) 0% 25%,transparent 0% 50%);
  background-size:22px 22px;border-radius:var(--r-3);overflow:hidden;
  box-shadow:var(--shadow),inset 0 0 0 1px var(--shot-ring)}
.hero .shotbox img{width:100%;display:block;aspect-ratio:4/3;max-height:min(44vh,430px);
  object-fit:contain}
.hero .shotbox figcaption{position:absolute;inset:auto 0 0 0;color:#fff;font-size:var(--fs-sm);
  font-weight:550;padding:26px 12px 8px;background:linear-gradient(to top,var(--overlay),transparent);
  text-shadow:0 1px 2px rgba(0,0,0,.55)}
.linkrow{display:flex;flex-wrap:wrap;gap:6px 10px;margin-top:var(--s-3);font-size:var(--fs-sm);
  overflow-wrap:anywhere}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:var(--s-3);align-items:center}
.picked{background:var(--accent-soft)}
.picked td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
details.round{border-top:1px solid var(--line);padding:var(--s-2) 0}
details.round summary{cursor:pointer;font-size:var(--fs-sm)}
.jump{display:flex;flex-wrap:wrap;gap:6px;margin:var(--s-4) 0 0;position:sticky;top:52px;z-index:10;
  padding:6px 0;background:color-mix(in srgb,var(--bg) 92%,transparent)}
.jump a{font-size:var(--fs-sm);padding:3px 10px;border-radius:999px;background:var(--chip);
  color:var(--chip-fg);font-weight:550}
.jump a:hover{text-decoration:none;background:var(--accent-soft);color:var(--accent)}
.neighbours{display:flex;gap:6px;align-items:center}
.neighbours .btn.dead{opacity:.4;pointer-events:none}
ul.plain li{display:list-item}
#judge ul.plain li{margin:5px 0;line-height:1.55}
.footnav{display:flex;gap:var(--s-2);justify-content:space-between;margin:var(--s-5) 0 0}
.panel table{width:auto;min-width:min(100%,560px)}
.panel .tablewrap{width:fit-content;max-width:100%}
"""


#: judge severities, worst first — a triage reader must see "critical" before "minor"
SEVERITY_ORDER = {"critical": 0, "blocker": 0, "major": 1, "moderate": 2, "minor": 3, "nit": 4}
SEVERITY_CLASS = {"critical": "pill-fail", "blocker": "pill-fail", "major": "pill-fail",
                  "moderate": "pill-warn", "minor": "pill-warn"}


def _kv(key: str, value: str) -> str:
    return f"<div class='kv'><span class='k'>{esc(key)}</span><span class='v'>{value}</span></div>"


def _panel(title: str, inner: str, *, anchor: str = "", extra_head: str = "") -> str:
    ident = f" id='{esc(anchor)}'" if anchor else ""
    return f"<div class='panel'{ident}><h2>{esc(title)}{extra_head}</h2>{inner}</div>"


# --------------------------------------------------------------------------- sections
def _picked(entry: RunEntry, rec: RunRecord) -> RoundRecord | None:
    return next((r for r in rec.rounds if r.index == entry.picked_round), None)


def _hero(entry: RunEntry, urls: UrlMaker, rec: RunRecord) -> str:
    """One big legible view + the facts.  Every other angle is in ``renders`` below,
    which is where a grid belongs — the top of the page answers "what is this?"."""
    image = entry.hero or entry.sheet
    label = entry.hero_label or ("Contact sheet" if entry.sheet else "")
    shot = (f"<a href='{esc(urls.file(entry, image))}' title='open full size'>"
            f"<img src='{esc(urls.file(entry, image))}' "
            f"alt='{esc(label)} — {esc(entry.slug)}' loading='eager'>"
            f"<figcaption>{esc(label)}</figcaption></a>") if image else \
        "<div class='empty'>no render</div>"
    badges = (f"{tier_tag(entry)}{verdict_tag(entry)}"
              + (f"<span class='tag' title='why the run stopped'>{esc(entry.status)}</span>" if entry.status else "")
              + f"<span class='tag'>{esc(entry.track)}</span><span class='tag'>{esc(entry.language)}</span>")
    delta = ("—" if entry.score is None or entry.baseline_score is None
             else format(entry.score - entry.baseline_score, "+.3f"))
    facts = "".join([
        _kv("score", f"<b>{fmt(entry.score)}</b> (baseline {fmt(entry.baseline_score)}, Δ {delta})"),
        _kv("rounds", f"{entry.rounds} · picked r{entry.picked_round if entry.picked_round is not None else '–'}"),
        _kv("gate errors", str(entry.gate_errors) + (
            " — " + ", ".join(f"{k}:{v}" for k, v in entry.gate_summary.items()) if entry.gate_summary else "")),
        _kv("cost", f"${entry.cost_usd:.3f}" + (f" · {entry.minutes:.1f} min" if entry.minutes is not None else "")),
        _kv("generator", esc(entry.generator)),
        _kv("judge", esc(entry.judge)),
        _kv("planner", esc(rec.spec.backends.planner)),
        _kv("workspace", f"<code class='xs'>{esc(entry.path)}</code>"),
    ])
    links = " · ".join(f"<a href='{esc(urls.link(entry, link))}'>{esc(link.label)}</a>" for link in entry.links)
    prompt = f"<p>{esc(entry.prompt)}</p>"
    caption = f"<p class='small faint'>{esc(entry.caption)}</p>" if entry.caption else ""
    err = f"<div class='err'>{esc(entry.error)}</div>" if entry.error else ""
    return (f"<div class='hero'><div class='shotbox'>{shot}</div>"
            f"<div class='panel' style='margin:0'><div class='badges'>{badges}</div>{prompt}{caption}{err}"
            f"<div class='kvs' style='margin-top:var(--s-3)'>{facts}</div>"
            f"<div class='linkrow'>{links}</div></div></div>")


def _rounds_table(entry: RunEntry, urls: UrlMaker) -> str:
    head = ("<tr><th>r</th><th>kind</th><th class='n'>score</th><th>verdict</th><th class='n'>gates</th>"
            "<th>build</th><th class='n'>$</th><th class='n'>min</th><th>commit</th><th>views</th></tr>")
    rows = []
    for r in entry.round_rows:
        verdict = "—" if r.passed is None else ("pass" if r.passed else "fail")
        build = "—" if r.build_ok is None else ("ok" if r.build_ok else "failed")
        gates = str(r.gate_errors) + (" (" + ", ".join(f"{k}:{v}" for k, v in r.gates.items()) + ")" if r.gates else "")
        sheet = f"<a href='{esc(urls.file(entry, r.sheet))}'>all views</a>" if r.sheet else "—"
        picked = " class='picked'" if r.index == entry.picked_round else ""
        rows.append(f"<tr{picked}><td class='n'>{r.index}</td><td>{esc(r.kind)}</td>"
                    f"<td class='n'>{fmt(r.score)}</td><td>{verdict}</td><td class='n'>{esc(gates)}</td>"
                    f"<td>{build}</td><td class='n'>{r.cost_usd:.3f}</td><td class='n'>{r.minutes:.1f}</td>"
                    f"<td><code class='xs'>{esc(r.commit)}</code></td><td>{sheet}</td></tr>")
    body = "".join(rows) or "<tr><td colspan='10' class='faint'>no rounds recorded</td></tr>"
    return _panel(f"rounds ({len(entry.round_rows)})",
                  f"<div class='tablewrap' style='display:block'><table><thead>{head}</thead>"
                  f"<tbody>{body}</tbody></table></div>", anchor="rounds")


def _judgment_panel(entry: RunEntry, rec: RunRecord) -> str:
    rnd = _picked(entry, rec)
    j = effective_judgment(rnd) if rnd is not None else None
    if j is None:
        return _panel("judge", "<p class='faint small'>no round has a (non-degraded) verdict.</p>",
                      anchor="judge")
    scores = "".join(_kv(k, f"{v:.3f}") for k, v in sorted(j.scores.items()))
    issues = "".join(
        f"<li><span class='tag {SEVERITY_CLASS.get(str(i.severity).lower(), '')}'>{esc(i.severity)}</span>"
        f" <b>{esc(i.target)}</b> · {esc(i.kind)} — {esc(i.detail)}"
        + (f" <span class='faint'>[{esc(i.evidence)}]</span>" if i.evidence else "") + "</li>"
        for i in sorted(j.issues, key=lambda i: SEVERITY_ORDER.get(str(i.severity).lower(), 9))
    ) or "<li class='faint'>none reported</li>"
    plan = "".join(f"<li><b>p{it.priority}</b> {esc(it.target)} — {esc(it.instruction)}</li>"
                   for it in sorted(j.improvement_plan, key=lambda i: i.priority)) or "<li class='faint'>none</li>"
    strengths = "".join(f"<li>{esc(s)}</li>" for s in j.strengths)
    accept = "".join(f"<span class='tag {'pill-pass' if ok else 'pill-fail'}'>{esc(k)}</span>"
                     for k, ok in sorted(j.acceptance_results.items()))
    inner = (
        f"<p class='small muted'>rubric <code>{esc(j.rubric)}</code> · {esc(j.judge_backend)} · "
        f"n={j.n_samples} · std {j.score_std:.3f} · overall <b>{j.overall:.3f}</b> "
        f"(the judge: {'pass' if j.passed else 'fail'})</p>"
        f"<p style='margin-top:var(--s-2)'>{esc(j.summary)}</p>"
        f"<div class='kvs' style='margin-top:var(--s-3)'>{scores}</div>"
        + (f"<h3 style='margin-top:var(--s-4)'>strengths</h3><ul class='plain'>{strengths}</ul>" if strengths else "")
        + f"<h3 style='margin-top:var(--s-4)'>issues ({len(j.issues)})</h3><ul class='plain'>{issues}</ul>"
        + f"<h3 style='margin-top:var(--s-4)'>improvement plan</h3><ul class='plain'>{plan}</ul>"
        + (f"<h3 style='margin-top:var(--s-4)'>acceptance</h3><div class='facts'>{accept}</div>" if accept else "")
    )
    return _panel(f"judge — picked round r{rnd.index}", inner, anchor="judge")


def _measurement_panel(entry: RunEntry, rec: RunRecord) -> str:
    rnd = _picked(entry, rec)
    m = rnd.measurement if rnd is not None else None
    if m is None:
        m = next((r.measurement for r in reversed(rec.rounds) if r.measurement is not None), None)
    if m is None:
        return ""
    def vec(v) -> str:
        return " ".join(f"{x:+.3f}" for x in v)
    rows = "".join([
        _kv("extents (m)", vec(m.extents)), _kv("bbox min", vec(m.bbox_min)), _kv("bbox max", vec(m.bbox_max)),
        _kv("centre", vec(m.center)), _kv("triangles", f"{m.tri_count:,}"), _kv("meshes", str(m.n_meshes)),
        _kv("islands", str(m.n_islands)), _kv("materials", str(m.materials)),
        _kv("ground gap (m)", f"{m.ground_gap_m:+.4f}"), _kv("footprint offset (m)", f"{m.footprint_offset_m:.4f}"),
        _kv("frame", esc(m.frame)),
    ])
    parts = ""
    if m.parts:
        head = "<tr><th>part</th><th class='n'>tris</th><th class='n'>islands</th><th>extents (m)</th></tr>"
        body = "".join(
            f"<tr><td>{esc(p.name)}</td><td class='n'>{p.tri_count:,}</td><td class='n'>{p.islands}</td>"
            f"<td class='n'>{' '.join(f'{b - a:.3f}' for a, b in zip(p.bbox_min, p.bbox_max, strict=False))}</td></tr>"
            for p in m.parts)
        parts = (f"<div class='tablewrap' style='display:block;margin-top:var(--s-3)'>"
                 f"<table><thead>{head}</thead><tbody>{body}</tbody></table></div>")
    return _panel("measurement", f"<div class='kvs'>{rows}</div>{parts}", anchor="measurement")


#: axis -> (label, format) for the complexity panel, in reading order
_CX_ROWS = (
    ("part_count", "parts", "{:.0f}"),
    ("assembly_depth", "sub-assembly depth", "{:.0f}"),
    ("tri_count", "triangles", "{:,.0f}"),
    ("materials", "distinct materials", "{:.0f}"),
    ("silhouette", "silhouette P²/4πA", "{:.1f}"),
    ("feature_density", "feature density", "{:.0f}"),
    ("symmetry_groups", "repeat groups", "{:.0f}"),
    ("hollowness", "hollowness", "{:.2f}"),
)


def _complexity_panel(entry: RunEntry, rec: RunRecord) -> str:
    """What was BUILT, measured without a VLM — the difficulty half of the verdict.

    Read it next to the judge panel: a high score on a low index is an easy win,
    a low score on a high index is the framework hitting its ceiling."""
    block = rec.extra.get("complexity")
    if entry.complexity is None and not isinstance(block, dict):
        return ""
    block = block if isinstance(block, dict) else {}
    index = entry.complexity if entry.complexity is not None else block.get("index")
    rows = [_kv("index", f"{float(index):.3f} ({esc(entry.complexity_band or block.get('band', ''))})")]
    for axis, label, spec in _CX_ROWS:
        v = entry.complexity_axes.get(axis, block.get(axis))
        if isinstance(v, (int, float)):
            rows.append(_kv(label, spec.format(float(v))))
    plan_parts = block.get("plan_parts")
    if isinstance(plan_parts, int) and plan_parts:
        rows.append(_kv("plan parts", str(plan_parts)))
        built = entry.complexity_axes.get("part_count", block.get("part_count"))  # the picked round's
        if isinstance(built, (int, float)):
            rows.append(_kv("built / planned parts", f"{float(built) / plan_parts:.2f}"))
    trail = block.get("by_round")
    if isinstance(trail, list) and len(trail) > 1:
        rows.append(_kv("by round", " → ".join(f"{float(x):.2f}" for x in trail)))
    note = ("<p class='xs faint'>objective complexity of the picked round's artifact "
            "(codeverse3d/spatial/complexity.py) — difficulty, not quality; see eval/docs/COMPLEXITY.md</p>")
    return _panel("complexity", f"<div class='kvs'>{''.join(rows)}</div>{note}", anchor="complexity")


def _renders_panel(entry: RunEntry, urls: UrlMaker, rec: RunRecord) -> str:
    ws = Workspace(entry.path)
    rnd = _picked(entry, rec)
    figs: list[tuple[str, str]] = []
    if rnd is not None and rnd.renders is not None:
        for v in rnd.renders.views:
            rel = _rel(ws, v.path)
            if rel and (ws.root / rel).is_file():
                figs.append((v.name, rel))
    figs += [(link.label, link.rel) for link in entry.links
             if link.label in ("articulation", "preview.gif") and link.rel]
    if not figs:
        return ""
    cells = "".join(gallery_figure(label, urls.file(entry, rel), urls.file(entry, rel))
                    for label, rel in figs)
    sheet = (f" <a class='small' href='{esc(urls.file(entry, entry.sheet))}'>· contact sheet</a>"
             if entry.sheet else "")
    return _panel(f"renders ({len(figs)})", f"<div class='shots'>{cells}</div>",
                  anchor="renders", extra_head=sheet)


def _cost_panel(entry: RunEntry, ws: Workspace, rec: RunRecord) -> str:
    from codeverse3d.record.telemetry import load_telemetry

    tele = load_telemetry(ws, rec)
    cost = tele.cost if tele is not None else None
    if cost is None:
        # old runs have no telemetry/: fall back to the record's own totals + per-round usage
        rows = "".join(_kv(k, f"${v:.4f}") for k, v in sorted(entry.cost_by_stage.items(), key=lambda kv: -kv[1]))
        u = rec.total_usage
        rows += "".join([_kv("total", f"${u.cost_usd:.4f}"),
                         _kv("input tokens", f"{u.input_tokens:,}"), _kv("output tokens", f"{u.output_tokens:,}")])
        per_round = "".join(_kv(f"r{r.index} {r.kind}", f"${r.usage.cost_usd:.4f} · {r.minutes:.1f} min")
                            for r in rec.rounds)
        return _panel("cost", f"<div class='kvs'>{rows}</div>"
                              f"<h3 style='margin-top:var(--s-4)'>per round</h3><div class='kvs'>{per_round}</div>",
                      anchor="cost")
    head = ("<tr><th>stage</th><th class='n'>calls</th><th class='n'>in tok</th><th class='n'>out tok</th>"
            "<th class='n'>cached</th><th class='n'>$</th><th class='n'>s</th></tr>")
    body = "".join(
        f"<tr><td>{esc(s.stage)}</td><td class='n'>{s.calls}</td><td class='n'>{s.input_tokens:,}</td>"
        f"<td class='n'>{s.output_tokens:,}</td><td class='n'>{s.cached_tokens:,}</td>"
        f"<td class='n'>{s.cost_usd:.4f}</td><td class='n'>{s.seconds:.0f}</td></tr>"
        for s in sorted(cost.by_stage, key=lambda s: -s.cost_usd))
    totals = "".join([
        _kv("total", f"${cost.total_usd:.4f}"),

        _kv("calls", str(cost.n_calls)),
        _kv("minutes", "—" if rec.minutes is None else f"{rec.minutes:.1f}"),
    ])
    by_model = "".join(_kv(k, f"${v:.4f}") for k, v in sorted(cost.by_model.items(), key=lambda kv: -kv[1]))
    by_role = "".join(_kv(k, f"${v:.4f}") for k, v in sorted(cost.by_role.items(), key=lambda kv: -kv[1]))
    inner = (f"<div class='kvs'>{totals}</div>"
             f"<div class='tablewrap' style='display:block;margin-top:var(--s-3)'>"
             f"<table><thead>{head}</thead><tbody>{body}</tbody></table></div>"
             f"<h3 style='margin-top:var(--s-4)'>by model</h3><div class='kvs'>{by_model}</div>"
             f"<h3 style='margin-top:var(--s-4)'>by role</h3><div class='kvs'>{by_role}</div>")
    return _panel("cost breakdown", inner, anchor="cost")


def _code_panel(entry: RunEntry, urls: UrlMaker, rec: RunRecord) -> str:
    run = Path(entry.path)
    files = src_files(run)
    if not files:
        return ""
    listing = "".join(
        f"<li><a href='{esc(urls.code(entry, rel))}'>{esc(rel)}</a>"
        f"<a class='sz' href='{esc(urls.file(entry, rel))}'>{size:,} B · raw</a></li>" for rel, size in files)
    entry_rel = ENTRY_FILE.get(rec.spec.language, "")
    shown = ""
    target = run / entry_rel if entry_rel else None
    if target is not None and target.is_file():
        text, note = read_text(target)
        shown = (f"<h3 style='margin-top:var(--s-4)'>{esc(entry_rel)}"
                 f" <span class='faint small'>{esc(note)}</span></h3>"
                 + numbered(text, suffix=target.suffix))
    return _panel(f"code ({len(files)} files)", f"<ul class='filelist'>{listing}</ul>{shown}", anchor="code")


# --------------------------------------------------------------------------- page
def _neighbours(urls: UrlMaker, prev: RunEntry | None, nxt: RunEntry | None) -> str:
    """Triage is sequential: the next bad run is one click away, not a trip home."""
    def one(target: RunEntry | None, glyph: str, side: str, ident: str) -> str:
        if target is None:
            return f"<span class='btn dead' aria-hidden='true'>{glyph}</span>"
        label = target.title or target.slug
        return (f"<a class='btn' id='{ident}' href='{esc(urls.detail(target))}' "
                f"title='{side} run in this battery: {esc(label)}  (arrow key)'>{glyph}</a>")
    return (f"<span class='neighbours'>{one(prev, '← prev', 'previous', 'nav-prev')}"
            f"{one(nxt, 'next →', 'next', 'nav-next')}</span>")


def _foot_nav(urls: UrlMaker, prev: RunEntry | None, nxt: RunEntry | None) -> str:
    """The same walk, repeated where the reader actually finishes reading."""
    def one(target: RunEntry | None, glyph: str) -> str:
        if target is None:
            return "<span></span>"
        return (f"<a class='btn' href='{esc(urls.detail(target))}'>{glyph} "
                f"{esc(target.title or target.slug)}</a>")
    if prev is None and nxt is None:
        return ""
    return f"<nav class='footnav'>{one(prev, '←')}{one(nxt, '→')}</nav>"


#: ← / → walk the battery; typing in a field is never hijacked
NEIGHBOUR_JS = """
(function(){var p=document.getElementById('nav-prev'),n=document.getElementById('nav-next');
document.addEventListener('keydown',function(e){
  var t=e.target||{}; if(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.tagName==='SELECT') return;
  if(e.metaKey||e.ctrlKey||e.altKey) return;
  if(e.key==='ArrowLeft'&&p&&p.href) location.href=p.href;
  if(e.key==='ArrowRight'&&n&&n.href) location.href=n.href;
});})();
"""


def render_detail(entry: RunEntry, urls: UrlMaker, ws: Workspace, rec: RunRecord, *,
                  prev: RunEntry | None = None, nxt: RunEntry | None = None) -> str:
    """Full detail page for a run whose record parsed."""
    nav = "".join(f"<a href='#{a}'>{a}</a>" for a in
                  ("rounds", "judge", "complexity", "measurement", "renders", "cost", "code"))
    body = (
        top_bar("3dcode gallery",
                crumbs=f"<a href='/'>gallery</a> <span class='faint'>/</span> "
                       f"<a href='/?battery={quote(entry.battery)}'>{esc(entry.battery)}</a> "
                       f"<span class='faint'>/</span> <b>{esc(entry.slug)}</b>",
                right=_neighbours(urls, prev, nxt) if urls.has_detail else "")
        + "<main class='wrap'>"
        + _hero(entry, urls, rec)
        + f"<nav class='jump' aria-label='sections'>{nav}</nav>"
        + _rounds_table(entry, urls)
        + _judgment_panel(entry, rec)
        + _complexity_panel(entry, rec)
        + _measurement_panel(entry, rec)
        + _renders_panel(entry, urls, rec)
        + _cost_panel(entry, ws, rec)
        + _code_panel(entry, urls, rec)
        + (_foot_nav(urls, prev, nxt) if urls.has_detail else "")
        + "</main>"
        + footer(f"{entry.path} · record.json re-read on every request")
    )
    return page_shell(f"{entry.slug} — 3dcode gallery", body, extra_css=DETAIL_CSS,
                      scripts=NEIGHBOUR_JS)


def render_broken_detail(entry: RunEntry, urls: UrlMaker) -> str:
    """Detail page for a run with no usable record — still links every file."""
    links = " · ".join(f"<a href='{esc(urls.link(entry, link))}'>{esc(link.label)}</a>" for link in entry.links)
    body = (top_bar("3dcode gallery", entry.battery,
                    crumbs=f"<a href='/'>gallery</a> <span class='faint'>/</span> <b>{esc(entry.slug)}</b>")
            + "<main class='wrap'>"
            + _panel(entry.slug, f"<div class='err'>{esc(entry.state)}: {esc(entry.error)}</div>"
                                 f"<p style='margin-top:var(--s-3)'>{esc(entry.prompt)}</p>"
                                 f"<div class='linkrow'>{links}</div>")
            + "</main>" + footer(entry.path))
    return page_shell(f"{entry.slug} — 3dcode gallery", body, extra_css=DETAIL_CSS)
