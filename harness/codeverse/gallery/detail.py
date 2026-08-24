"""The per-run detail page (``/run/<battery>/<slug>``).

Everything the harness recorded about one run, in the order you actually ask
for it: what it produced, how each round went, what the judge said about the
best round, what the deterministic measurement says, every render, where the
money went, and the code itself.  The record is re-read per request, so a run a
bench is still writing shows its latest rounds without a restart.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.common import ENTRY_FILE
from codeverse.contracts.run import RunRecord
from codeverse.flywheel.record import effective_judgment
from codeverse.gallery.code import CODE_CSS, numbered, read_text, src_files
from codeverse.gallery.index import best_sheet
from codeverse.gallery.model import RunEntry
from codeverse.gallery.theme import esc, footer, page_shell, top_bar
from codeverse.gallery.urls import UrlMaker
from codeverse.workspace import Workspace

DETAIL_CSS = CODE_CSS + """
.hero{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:var(--s-4);align-items:start}
@media (max-width:900px){.hero{grid-template-columns:1fr}}
.hero .shotbox{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-3);
  overflow:hidden;box-shadow:var(--shadow)}
.hero .shotbox img{width:100%;display:block;background:var(--sunken)}
.linkrow{display:flex;flex-wrap:wrap;gap:6px 10px;margin-top:var(--s-3);font-size:var(--fs-sm);
  overflow-wrap:anywhere}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:var(--s-3)}
.best{background:var(--accent-soft)}
details.round{border-top:1px solid var(--line);padding:var(--s-2) 0}
details.round summary{cursor:pointer;font-size:var(--fs-sm)}
"""


def _fmt(v: float | None, d: int = 3) -> str:
    return "—" if v is None else format(v, f".{d}f")


def _kv(key: str, value: str) -> str:
    return f"<div class='kv'><span class='k'>{esc(key)}</span><span class='v'>{value}</span></div>"


def _panel(title: str, inner: str, *, anchor: str = "") -> str:
    ident = f" id='{esc(anchor)}'" if anchor else ""
    return f"<div class='panel'{ident}><h2>{esc(title)}</h2>{inner}</div>"


def _rel(run: Path, path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    p = p if p.is_absolute() else run / p
    try:
        return p.resolve().relative_to(run.resolve()).as_posix()
    except (ValueError, OSError):
        return ""


# --------------------------------------------------------------------------- sections
def _hero(entry: RunEntry, urls: UrlMaker, rec: RunRecord) -> str:
    sheet = entry.sheet
    shot = (f"<a href='{esc(urls.file(entry, sheet))}'><img src='{esc(urls.file(entry, sheet))}' "
            f"alt='contact sheet — {esc(entry.slug)}' loading='eager'></a>") if sheet else \
        "<div class='empty'>no contact sheet</div>"
    badges = (f"<span class='tag tier {esc(entry.tier)}'>{esc(entry.tier)}</span>"
              f"<span class='tag {'pill-pass' if entry.passed else ('pill-fail' if entry.passed is False else '')}'>"
              f"{'passed' if entry.passed else ('failed' if entry.passed is False else 'unjudged')}</span>"
              f"<span class='tag'>{esc(entry.status)}</span>"
              f"<span class='tag'>{esc(entry.track)}</span><span class='tag'>{esc(entry.language)}</span>")
    delta = ("—" if entry.score is None or entry.baseline_score is None
             else format(entry.score - entry.baseline_score, "+.3f"))
    facts = "".join([
        _kv("score", f"<b>{_fmt(entry.score)}</b> (baseline {_fmt(entry.baseline_score)}, Δ {delta})"),
        _kv("rounds", f"{entry.rounds} · best r{entry.best_round if entry.best_round is not None else '–'}"),
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
            "<th>build</th><th class='n'>$</th><th class='n'>s</th><th>commit</th><th>sheet</th></tr>")
    rows = []
    for r in entry.round_rows:
        verdict = "—" if r.passed is None else ("pass" if r.passed else "fail")
        build = "—" if r.build_ok is None else ("ok" if r.build_ok else "failed")
        gates = str(r.gate_errors) + (" (" + ", ".join(f"{k}:{v}" for k, v in r.gates.items()) + ")" if r.gates else "")
        sheet = f"<a href='{esc(urls.file(entry, r.sheet))}'>sheet</a>" if r.sheet else "—"
        best = " class='best'" if r.index == entry.best_round else ""
        rows.append(f"<tr{best}><td class='n'>{r.index}</td><td>{esc(r.kind)}</td>"
                    f"<td class='n'>{_fmt(r.score)}</td><td>{verdict}</td><td class='n'>{esc(gates)}</td>"
                    f"<td>{build}</td><td class='n'>{r.cost_usd:.3f}</td><td class='n'>{r.duration_s:.0f}</td>"
                    f"<td><code class='xs'>{esc(r.commit)}</code></td><td>{sheet}</td></tr>")
    body = "".join(rows) or "<tr><td colspan='10' class='faint'>no rounds recorded</td></tr>"
    return _panel(f"rounds ({len(entry.round_rows)})",
                  f"<div class='tablewrap' style='display:block'><table><thead>{head}</thead>"
                  f"<tbody>{body}</tbody></table></div>", anchor="rounds")


def _judgment_panel(rec: RunRecord, best: int | None) -> str:
    rnd = next((r for r in rec.rounds if r.index == best), None)
    j = effective_judgment(rnd) if rnd is not None else None
    if j is None:
        return _panel("judge", "<p class='faint small'>the best round has no (non-degraded) verdict.</p>",
                      anchor="judge")
    scores = "".join(_kv(k, f"{v:.3f}") for k, v in sorted(j.scores.items()))
    issues = "".join(
        f"<li><b>{esc(i.severity)}</b> · {esc(i.target)} · {esc(i.kind)} — {esc(i.detail)}"
        + (f" <span class='faint'>[{esc(i.evidence)}]</span>" if i.evidence else "") + "</li>"
        for i in j.issues) or "<li class='faint'>none reported</li>"
    plan = "".join(f"<li><b>p{it.priority}</b> {esc(it.target)} — {esc(it.instruction)}</li>"
                   for it in sorted(j.improvement_plan, key=lambda i: i.priority)) or "<li class='faint'>none</li>"
    strengths = "".join(f"<li>{esc(s)}</li>" for s in j.strengths)
    accept = "".join(f"<span class='tag {'pill-pass' if ok else 'pill-fail'}'>{esc(k)}</span>"
                     for k, ok in sorted(j.acceptance_results.items()))
    inner = (
        f"<p class='small muted'>rubric <code>{esc(j.rubric)}</code> · {esc(j.judge_backend)} · "
        f"n={j.n_samples} · std {j.score_std:.3f} · overall <b>{j.overall:.3f}</b> "
        f"({'passed' if j.passed else 'failed'})</p>"
        f"<p style='margin-top:var(--s-2)'>{esc(j.summary)}</p>"
        f"<div class='kvs' style='margin-top:var(--s-3)'>{scores}</div>"
        + (f"<h3 style='margin-top:var(--s-4)'>strengths</h3><ul class='plain'>{strengths}</ul>" if strengths else "")
        + f"<h3 style='margin-top:var(--s-4)'>issues ({len(j.issues)})</h3><ul class='plain'>{issues}</ul>"
        + f"<h3 style='margin-top:var(--s-4)'>improvement plan</h3><ul class='plain'>{plan}</ul>"
        + (f"<h3 style='margin-top:var(--s-4)'>acceptance</h3><div class='facts'>{accept}</div>" if accept else "")
    )
    return _panel(f"judge — best round r{best}", inner, anchor="judge")


def _measurement_panel(rec: RunRecord, best: int | None) -> str:
    rnd = next((r for r in rec.rounds if r.index == best), None)
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


def _renders_panel(entry: RunEntry, urls: UrlMaker, rec: RunRecord, best: int | None) -> str:
    run = Path(entry.path)
    rnd = next((r for r in rec.rounds if r.index == best), None)
    figs: list[tuple[str, str]] = []
    if rnd is not None and rnd.renders is not None:
        for v in rnd.renders.views:
            rel = _rel(run, v.path)
            if rel and (run / rel).is_file():
                label = v.name + (f" · t={v.time_s:g}s" if v.time_s is not None else "")
                figs.append((label, rel))
    extra = [(link.label, link.rel) for link in entry.links
             if link.label in ("articulation", "preview.gif") and link.rel]
    for label, rel in extra:
        figs.append((label, rel))
    if not figs:
        return ""
    cells = "".join(
        f"<figure><a href='{esc(urls.file(entry, rel))}'>"
        f"<img loading='lazy' src='{esc(urls.file(entry, rel))}' alt='{esc(label)}'></a>"
        f"<figcaption>{esc(label)}</figcaption></figure>" for label, rel in figs)
    return _panel(f"renders ({len(figs)})", f"<div class='shots'>{cells}</div>", anchor="renders")


def _cost_panel(entry: RunEntry, ws: Workspace, rec: RunRecord) -> str:
    from codeverse.flywheel.telemetry import load_telemetry

    tele = load_telemetry(ws, rec)
    cost = tele.cost if tele is not None else None
    if cost is None:
        # old runs have no telemetry/: fall back to the record's own totals + per-round usage
        rows = "".join(_kv(k, f"${v:.4f}") for k, v in sorted(entry.cost_by_stage.items(), key=lambda kv: -kv[1]))
        u = rec.total_usage
        rows += "".join([_kv("total", f"${u.cost_usd:.4f}"),
                         _kv("input tokens", f"{u.input_tokens:,}"), _kv("output tokens", f"{u.output_tokens:,}")])
        per_round = "".join(_kv(f"r{r.index} {r.kind}", f"${r.usage.cost_usd:.4f} · {r.duration_s:.0f}s")
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
        _kv("budget", f"${cost.budget_usd:.2f}" + (f" ({cost.budget_used_pct:.0f}% used)"
                                                   if cost.budget_used_pct is not None else "")),
        _kv("calls", str(cost.n_calls)),
        _kv("wall clock", f"{cost.wall_clock_s / 60:.1f} min"),
        _kv("unattributed", f"${cost.unattributed_usd:.4f}"),
        _kv("post-run", f"${cost.post_run_usd:.4f}"),
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
def render_detail(entry: RunEntry, urls: UrlMaker, ws: Workspace, rec: RunRecord) -> str:
    """Full detail page for a run whose record parsed."""
    best = entry.best_round
    if not entry.sheet:
        entry = entry.model_copy(update={"sheet": best_sheet(ws, rec)})
    nav = " · ".join(f"<a href='#{a}'>{a}</a>" for a in
                     ("rounds", "judge", "measurement", "renders", "cost", "code"))
    body = (
        top_bar("3dcv gallery",
                crumbs=f"<a href='/'>gallery</a> <span class='faint'>/</span> "
                       f"<a href='/?battery={esc(entry.battery)}'>{esc(entry.battery)}</a> "
                       f"<span class='faint'>/</span> <b>{esc(entry.slug)}</b>")
        + "<main class='wrap'>"
        + _hero(entry, urls, rec)
        + f"<p class='small muted' style='margin-top:var(--s-4)'>jump to: {nav}</p>"
        + _rounds_table(entry, urls)
        + _judgment_panel(rec, best)
        + _measurement_panel(rec, best)
        + _renders_panel(entry, urls, rec, best)
        + _cost_panel(entry, ws, rec)
        + _code_panel(entry, urls, rec)
        + "</main>"
        + footer(f"{entry.path} · record.json re-read on every request")
    )
    return page_shell(f"{entry.slug} — 3dcv gallery", body, extra_css=DETAIL_CSS)


def render_broken_detail(entry: RunEntry, urls: UrlMaker) -> str:
    """Detail page for a run with no usable record — still links every file."""
    links = " · ".join(f"<a href='{esc(urls.link(entry, link))}'>{esc(link.label)}</a>" for link in entry.links)
    body = (top_bar("3dcv gallery", entry.battery,
                    crumbs=f"<a href='/'>gallery</a> <span class='faint'>/</span> <b>{esc(entry.slug)}</b>")
            + "<main class='wrap'>"
            + _panel(entry.slug, f"<div class='err'>{esc(entry.state)}: {esc(entry.error)}</div>"
                                 f"<p style='margin-top:var(--s-3)'>{esc(entry.prompt)}</p>"
                                 f"<div class='linkrow'>{links}</div>")
            + "</main>" + footer(entry.path))
    return page_shell(f"{entry.slug} — 3dcv gallery", body, extra_css=DETAIL_CSS)
