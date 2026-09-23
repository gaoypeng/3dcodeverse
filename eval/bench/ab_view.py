#!/usr/bin/env python3
"""Render an A/B experiment round as ONE self-contained page you can look at.

    python bench/ab_view.py bench/out/<battery>/runs --a cb_a --b cb_b \
        --title "urdf cookbook" --a-label "no cookbook" --b-label "cookbook delivered" \
        --out /tmp/ab.html

Every A/B here is decided on numbers, but a score is a summary of an image and the owner
wants the image.  Runs are PAIRED on their brief — the only thing that makes two of them
comparable — and each pair is shown blind: the arm labels are hidden and the side is
swapped on a hash of the brief, so the eye is not primed by knowing which is the new one.
Vote per pair, then reveal; the page tallies where you and the judge disagree and writes
the whole read into a box you can paste back.  Votes live in localStorage, so a reload
keeps them.  The verdict at the top still refuses to name a winner the sample cannot
support.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench._jsonl import latest  # noqa: E402
from bench.stats import mean_ci  # noqa: E402
from codeverse3d.addons import select  # noqa: E402
from codeverse3d.record.record import load_record  # noqa: E402

SHEET_W = 1100


@dataclass
class Run:
    slug: str
    arm: str
    brief: str = ""
    status: str = "?"
    picked: float | None = None
    baseline: float | None = None
    rounds: int = 0
    usd: float = 0.0
    minutes: int = 0             # RunRecord.minutes (docs/COST.md §31), rounded
    error: str = ""
    sheet: str = ""              # data URI
    notes: list[str] = field(default_factory=list)


def _sheet_data_uri(run_dir: Path) -> str:
    try:
        from PIL import Image
    except ImportError:                                     # pragma: no cover
        return ""
    for cand in (run_dir / "deliverable" / "sheet.png",
                 *sorted(run_dir.glob("artifacts/tool_renders/*/sheet.png")),
                 *sorted(run_dir.glob("artifacts/tool_renders/*/*sheet*.png")),
                 *sorted(run_dir.glob("rounds/*/renders/*sheet*.png"))):
        if not cand.is_file():
            continue
        im = Image.open(cand).convert("RGB")
        w, h = im.size
        s = min(1.0, SHEET_W / w)
        im = im.resize((int(w * s), int(h * s)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=78, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return ""


def load_run(run_dir: Path, arm: str) -> Run:
    r = Run(slug=run_dir.name, arm=arm)
    try:  # read once: the round addons/select picks, the ledger's total, the steps' minutes
        rec = load_record(run_dir)
        s = select.summarise(run_dir, record=rec)
    except Exception:  # noqa: BLE001 - a missing or unreadable record must not blank the page
        rec = None
    if rec is not None:
        r.brief, r.rounds, r.error = rec.spec.prompt, len(rec.rounds), (rec.error or "")[:300]
        r.status, r.picked, r.baseline = s.stop_reason or "?", s.picked_score, s.baseline_score
        r.usd, r.minutes = round(rec.total_usage.cost_usd, 2), round(rec.minutes or 0)
        for rd in rec.rounds:
            failed = [g.gate for g in rd.gates if not g.passed]
            if failed:
                r.notes.append(f"r{rd.index} gates: {', '.join(failed[:4])}")
    r.sheet = _sheet_data_uri(run_dir)
    return r


#: scored pairs below which this function will not name a winner, whatever the numbers
#: say.  A reference harness on the same model tier measured a run-to-run score sd of
#: 0.159 against a re-judge sd of 0.013, giving a minimum detectable effect of 0.081 at
#: n=30 — and it published, then retracted, several prompt findings because a promising
#: first replicate turned out to be noise pointing the right way (interpenetration −0.40
#: in replicate 1, +0.03 in replicate 2, pooled p=0.405).
MIN_PAIRS = 3


def paired_by_brief(a: list[Run], b: list[Run]) -> dict[str, tuple[Run | None, Run | None]]:
    """``{brief: (A run, B run)}`` — the pairing the page shows and the verdict reads.  A
    brief run twice in one arm (a redo) is its LAST run, the rule of every bench journal."""
    ka, kb = (latest(rs, key=lambda r: r.brief or r.slug) for rs in (a, b))
    return {k: (ka.get(k), kb.get(k)) for k in {**ka, **kb}}


def verdict(a: list[Run], b: list[Run]) -> tuple[str, str]:
    """(headline, why): B − A per paired brief and the 95 % t-interval of its mean
    (``bench/stats.py``) — no winner unless that interval excludes zero."""
    deltas = [rb.picked - ra.picked for ra, rb in paired_by_brief(a, b).values()
              if ra is not None and rb is not None and ra.picked is not None and rb.picked is not None]
    if len(deltas) < MIN_PAIRS:
        scored_a, scored_b = (sum(x.picked is not None for x in rs) for rs in (a, b))
        return ("Inconclusive — not enough scored pairs",
                f"{len(deltas)} brief(s) scored in both arms (arm A scored {scored_a} of {len(a)} runs, arm B "
                f"{scored_b} of {len(b)}); this needs at least {MIN_PAIRS} before a winner is named. A run that "
                "died before it scored is not an observation about the arm — it is a missing one.")
    ci = mean_ci(deltas)
    if not ci.separated:
        return (f"Inconclusive — Δ {ci.mean:+.3f} ± {ci.half:.3f} includes zero",
                f"B − A over {ci.n} paired briefs: the 95 % t-interval of the mean includes zero — the pairs "
                "disagree with each other by more than the mean moved, so this is not evidence.")
    return (f"B {'wins' if ci.mean > 0 else 'loses'} by {ci.mean:+.3f} ± {ci.half:.3f}",
            f"B − A over {ci.n} paired briefs, 95 % t-interval. Worth a confirming replicate before it is believed.")


def _num(v: object) -> str:
    return "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def _stats(rs: list[Run]) -> str:
    sc = [x.picked for x in rs if x.picked is not None]
    if not sc:
        return "no scored run"
    return (f"n={len(sc)}/{len(rs)} · mean {statistics.mean(sc):.3f} · "
            f"median {statistics.median(sc):.3f} · σ {statistics.pstdev(sc):.3f} · "
            f"worst {min(sc):.3f}")


def _panel(r: Run | None, side: str) -> str:
    """One arm's render for one brief.  Carries its numbers in data- attributes so the
    page can keep them hidden until the reader has actually looked."""
    if r is None:
        return f'<div class="panel empty" data-side="{side}"><p>this arm has no run for this brief</p></div>'
    tone = {"max_rounds": "good", "failed": "bad"}.get(r.status, "warn")  # why it stopped, not a verdict
    img = (f'<button class="sheet" data-full="{r.sheet}" '
           f'aria-label="enlarge {html.escape(r.slug)}"><img src="{r.sheet}" '
           f'alt="rendered frames for {html.escape(r.slug)}" loading="lazy"></button>'
           if r.sheet else
           '<div class="sheet empty">nothing rendered — the run died before it built anything</div>')
    err = f'<p class="err">{html.escape(r.error)}</p>' if r.error else ""
    return f"""<div class="panel" data-side="{side}" data-arm="{r.arm}">
  <div class="ptag"><span class="side">{side.upper()}</span><span class="armname">arm {r.arm}</span></div>
  {img}
  <div class="nums">
    <span class="pill {tone}">{html.escape(r.status)}</span>
    <span class="n"><b>{_num(r.picked)}</b> score</span>
    <span class="n">{r.rounds} rounds</span>
    <span class="n">${r.usd:.2f}</span>
    <span class="n">{r.minutes} min</span>
    <span class="slug">{html.escape(r.slug)}</span>
  </div>{err}
</div>"""


def _pair(i: int, brief: str, a: Run | None, b: Run | None, flip: bool) -> str:
    left, right = (b, a) if flip else (a, b)
    da = "" if a is None or a.picked is None else f'{a.picked:.3f}'
    db = "" if b is None or b.picked is None else f'{b.picked:.3f}'
    return f"""<section class="pair" id="p{i}" data-i="{i}" data-flip="{'1' if flip else '0'}"
         data-a="{da}" data-b="{db}">
  <header class="pairhead">
    <span class="idx">{i + 1:02d}</span>
    <h3>{html.escape(brief)}</h3>
    <span class="agree" hidden></span>
  </header>
  <div class="two">{_panel(left, 'left')}{_panel(right, 'right')}</div>
  <div class="vote" role="group" aria-label="which render is better">
    <span class="vlabel">which is better?</span>
    <button data-v="left">◀ left</button>
    <button data-v="tie">tie</button>
    <button data-v="right">right ▶</button>
    <button data-v="skip" class="ghost">can't tell</button>
  </div>
</section>"""


def build(runs_dir: Path, a_prefix: str, b_prefix: str, *, title: str,
          a_label: str, b_label: str, changed: str) -> str:
    a = [load_run(d, "A") for d in sorted(runs_dir.iterdir()) if d.is_dir() and d.name.startswith(a_prefix)]
    b = [load_run(d, "B") for d in sorted(runs_dir.iterdir()) if d.is_dir() and d.name.startswith(b_prefix)]
    head, why = verdict(a, b)
    pairs = []
    for i, (brief, (ra, rb)) in enumerate(paired_by_brief(a, b).items()):
        # deterministic side-swap so the eye is not primed, stable across reloads
        flip = bool(sum(ord(c) for c in brief) % 2)
        pairs.append(_pair(i, brief, ra, rb, flip))

    return TEMPLATE.format(
        title=html.escape(title), head=html.escape(head), why=html.escape(why),
        changed=html.escape(changed), a_label=html.escape(a_label), b_label=html.escape(b_label),
        n_a=len(a), n_b=len(b), n_pairs=len(pairs),
        a_stats=html.escape(_stats(a)), b_stats=html.escape(_stats(b)),
        pairs="\n".join(pairs))


TEMPLATE = """<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --ground:#f7f6f3; --surface:#ffffff; --sunk:#efede8; --line:#dedbd4;
  --ink:#1b1d22; --dim:#6d7079; --faint:#9a9ca3;
  --armA:#4d7695; --armB:#b5762c; --accent:#b5762c;
  --good:#4a8659; --warn:#a8842e; --bad:#b0534d;
  --shadow:0 1px 2px rgba(20,22,28,.06), 0 8px 24px rgba(20,22,28,.05);
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:"IBM Plex Sans",system-ui,-apple-system,Segoe UI,sans-serif;
  --cond:"IBM Plex Sans Condensed","IBM Plex Sans",system-ui,sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#131519; --surface:#1a1d23; --sunk:#0f1114; --line:#2b2f37;
    --ink:#e7e5e0; --dim:#9498a1; --faint:#6b6f78;
    --armA:#7fa8c7; --armB:#d9963f; --accent:#d9963f;
    --good:#6bab7b; --warn:#c9a244; --bad:#cf6f68;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#131519; --surface:#1a1d23; --sunk:#0f1114; --line:#2b2f37;
  --ink:#e7e5e0; --dim:#9498a1; --faint:#6b6f78;
  --armA:#7fa8c7; --armB:#d9963f; --accent:#d9963f;
  --good:#6bab7b; --warn:#c9a244; --bad:#cf6f68;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--ground); color:var(--ink); font-family:var(--sans);
       font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1360px; margin:0 auto; padding:0 24px 96px; }}
h1,h2,h3 {{ font-family:var(--cond); font-weight:700; text-wrap:balance; margin:0; letter-spacing:-.01em; }}

/* ── masthead ─────────────────────────────────────────── */
header.top {{ padding:44px 0 26px; border-bottom:1px solid var(--line); }}
header.top h1 {{ font-size:clamp(28px,3.6vw,42px); line-height:1.08; }}
.changed {{ color:var(--dim); margin:10px 0 0; max-width:70ch; }}
.verdict {{ margin-top:26px; padding:18px 20px; background:var(--surface); border:1px solid var(--line);
            border-left:3px solid var(--accent); border-radius:3px; box-shadow:var(--shadow); }}
.verdict h2 {{ font-size:17px; }}
.verdict p {{ margin:7px 0 0; color:var(--dim); font-size:14px; max-width:82ch; }}
.arms {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; margin-top:16px; }}
.armbox {{ padding:13px 15px; background:var(--surface); border:1px solid var(--line); border-radius:3px; }}
.armbox .k {{ font-family:var(--cond); font-weight:700; font-size:13px; letter-spacing:.09em;
              text-transform:uppercase; }}
.armbox.A .k {{ color:var(--armA); }} .armbox.B .k {{ color:var(--armB); }}
.armbox .d {{ color:var(--ink); font-size:14px; margin-top:3px; }}
.armbox .s {{ font-family:var(--mono); font-size:12.5px; color:var(--dim); margin-top:6px;
              font-variant-numeric:tabular-nums; }}

/* ── sticky control bar ───────────────────────────────── */
.bar {{ position:sticky; top:0; z-index:30; display:flex; flex-wrap:wrap; gap:10px; align-items:center;
        padding:12px 0; margin-bottom:8px; background:color-mix(in srgb,var(--ground) 92%,transparent);
        backdrop-filter:blur(8px); border-bottom:1px solid var(--line); }}
button {{ font:inherit; font-size:13.5px; color:var(--ink); background:var(--surface);
          border:1px solid var(--line); border-radius:3px; padding:7px 13px; cursor:pointer;
          transition:border-color .12s, background .12s; }}
button:hover {{ border-color:var(--accent); }}
button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
button.on {{ background:var(--accent); border-color:var(--accent); color:#12140f; font-weight:600; }}
button.ghost {{ color:var(--faint); }}
.bar .prog {{ margin-left:auto; font-family:var(--mono); font-size:12.5px; color:var(--dim);
              font-variant-numeric:tabular-nums; }}

/* ── a pair ───────────────────────────────────────────── */
.pair {{ padding:30px 0; border-bottom:1px solid var(--line); scroll-margin-top:70px; }}
.pairhead {{ display:flex; align-items:baseline; gap:14px; margin-bottom:16px; }}
.pairhead .idx {{ font-family:var(--mono); font-size:12px; color:var(--faint);
                  font-variant-numeric:tabular-nums; }}
.pairhead h3 {{ font-size:19px; font-weight:600; flex:1; }}
.agree {{ font-family:var(--mono); font-size:12px; padding:3px 9px; border-radius:2px;
          border:1px solid var(--line); color:var(--dim); white-space:nowrap; }}
.agree.yes {{ color:var(--good); border-color:var(--good); }}
.agree.no {{ color:var(--bad); border-color:var(--bad); }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:860px) {{ .two {{ grid-template-columns:1fr; }} }}
.panel {{ background:var(--surface); border:1px solid var(--line); border-radius:3px;
          overflow:hidden; box-shadow:var(--shadow); }}
.panel.empty {{ display:grid; place-items:center; min-height:180px; color:var(--faint); padding:20px;
                text-align:center; }}
.ptag {{ display:flex; align-items:center; gap:9px; padding:9px 12px; border-bottom:1px solid var(--line);
         font-family:var(--cond); font-size:12px; letter-spacing:.1em; text-transform:uppercase; }}
.ptag .side {{ font-weight:700; color:var(--dim); }}
.ptag .armname {{ font-weight:700; }}
.panel[data-arm="A"] .armname {{ color:var(--armA); }}
.panel[data-arm="B"] .armname {{ color:var(--armB); }}
body.blind .armname, body.blind .slug {{ visibility:hidden; }}
body.blind .nums .n b, body.blind .pill {{ filter:blur(6px); user-select:none; }}
.sheet {{ display:block; width:100%; padding:0; margin:0; border:0; background:var(--sunk);
          cursor:zoom-in; border-radius:0; }}
.sheet:hover {{ border-color:transparent; }}
.sheet img {{ display:block; width:100%; height:auto; }}
.sheet.empty {{ display:grid; place-items:center; min-height:170px; color:var(--faint);
                font-size:13.5px; cursor:default; padding:20px; text-align:center; }}
.nums {{ display:flex; flex-wrap:wrap; align-items:center; gap:7px 14px; padding:11px 12px;
         font-family:var(--mono); font-size:12.5px; color:var(--dim);
         font-variant-numeric:tabular-nums; }}
.nums .n b {{ color:var(--ink); font-size:14px; }}
.nums .slug {{ margin-left:auto; color:var(--faint); }}
.pill {{ padding:2px 8px; border-radius:2px; border:1px solid currentColor; font-size:11px;
         letter-spacing:.05em; text-transform:uppercase; }}
.pill.good {{ color:var(--good); }} .pill.warn {{ color:var(--warn); }} .pill.bad {{ color:var(--bad); }}
.err {{ margin:0; padding:0 12px 11px; color:var(--bad); font-family:var(--mono); font-size:12px; }}
.vote {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:14px; }}
.vote .vlabel {{ font-family:var(--cond); font-size:12px; letter-spacing:.1em; text-transform:uppercase;
                 color:var(--faint); margin-right:4px; }}

/* ── tally ────────────────────────────────────────────── */
.tally {{ margin-top:36px; padding:22px; background:var(--surface); border:1px solid var(--line);
          border-radius:3px; box-shadow:var(--shadow); }}
.tally h2 {{ font-size:18px; }}
.tally p {{ color:var(--dim); font-size:14px; }}
.tally textarea {{ width:100%; min-height:150px; margin-top:12px; padding:13px; background:var(--sunk);
                   color:var(--ink); border:1px solid var(--line); border-radius:3px;
                   font-family:var(--mono); font-size:12.5px; line-height:1.6; resize:vertical; }}

/* ── lightbox ─────────────────────────────────────────── */
.lb {{ position:fixed; inset:0; z-index:100; display:none; place-items:center; padding:24px;
       background:rgba(8,9,12,.9); cursor:zoom-out; }}
.lb.open {{ display:grid; }}
.lb img {{ max-width:100%; max-height:100%; border-radius:2px; }}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
</style>

<div class="wrap">
<header class="top">
  <h1>{title}</h1>
  <p class="changed">{changed}</p>

  <div class="verdict">
    <h2>{head}</h2>
    <p>{why}</p>
  </div>

  <div class="arms">
    <div class="armbox A"><div class="k">arm A · control</div><div class="d">{a_label}</div>
      <div class="s">{a_stats}</div></div>
    <div class="armbox B"><div class="k">arm B · treatment</div><div class="d">{b_label}</div>
      <div class="s">{b_stats}</div></div>
  </div>
</header>

<div class="bar">
  <button id="blind" class="on">blind: on</button>
  <button id="reveal">reveal scores</button>
  <button id="clear" class="ghost">clear my votes</button>
  <span class="prog"><span id="done">0</span>/{n_pairs} judged</span>
</div>

{pairs}

<section class="tally">
  <h2>Your read</h2>
  <p>Judged blind, before the scores were visible. Copy this back into the session and it
     becomes the guidance for the next round.</p>
  <textarea id="out" readonly spellcheck="false"></textarea>
</section>
</div>

<div class="lb" id="lb"><img alt=""></div>

<script>
const KEY = "abvote:" + document.title;
let votes = {{}};
try {{ votes = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{ votes = {{}}; }}
const save = () => {{ try {{ localStorage.setItem(KEY, JSON.stringify(votes)); }} catch (e) {{}} }};
const pairs = [...document.querySelectorAll(".pair")];

/* a vote is cast on a SIDE; which arm that was depends on the flip this pair was built with */
const armOf = (p, side) => (p.dataset.flip === "1"
  ? (side === "left" ? "B" : "A") : (side === "left" ? "A" : "B"));

function paint() {{
  let n = 0;
  for (const p of pairs) {{
    const v = votes[p.dataset.i];
    if (v) n++;
    for (const b of p.querySelectorAll(".vote button")) b.classList.toggle("on", b.dataset.v === v);
    const tag = p.querySelector(".agree");
    const a = parseFloat(p.dataset.a), b = parseFloat(p.dataset.b);
    if (!document.body.classList.contains("blind") && v && v !== "skip"
        && !isNaN(a) && !isNaN(b)) {{
      const judge = Math.abs(b - a) < 0.02 ? "tie" : (b > a ? "B" : "A");
      const mine = v === "tie" ? "tie" : armOf(p, v);
      tag.hidden = false;
      tag.textContent = mine === judge ? "you agree with the judge" : "you and the judge disagree";
      tag.className = "agree " + (mine === judge ? "yes" : "no");
    }} else {{ tag.hidden = true; }}
  }}
  document.getElementById("done").textContent = n;
  report();
}}

function report() {{
  let A = 0, B = 0, tie = 0, skip = 0, agree = 0, cast = 0;
  const lines = [];
  for (const p of pairs) {{
    const v = votes[p.dataset.i];
    const brief = p.querySelector("h3").textContent;
    if (!v) {{ lines.push(`${{(+p.dataset.i + 1).toString().padStart(2, "0")}}  —        ${{brief}}`); continue; }}
    if (v === "skip") {{ skip++; lines.push(`${{(+p.dataset.i + 1).toString().padStart(2, "0")}}  can't tell  ${{brief}}`); continue; }}
    const mine = v === "tie" ? "tie" : armOf(p, v);
    cast++;
    if (mine === "A") A++; else if (mine === "B") B++; else tie++;
    const a = parseFloat(p.dataset.a), b = parseFloat(p.dataset.b);
    let j = "";
    if (!isNaN(a) && !isNaN(b)) {{
      const judge = Math.abs(b - a) < 0.02 ? "tie" : (b > a ? "B" : "A");
      if (mine === judge) agree++;
      j = `   judge ${{judge}} (A ${{a.toFixed(3)}} / B ${{b.toFixed(3)}})${{mine === judge ? "" : "  ← DISAGREE"}}`;
    }} else {{ j = "   judge: no score"; }}
    lines.push(`${{(+p.dataset.i + 1).toString().padStart(2, "0")}}  ${{mine.padEnd(9)}} ${{brief}}${{j}}`);
  }}
  const head = [
    `human read of ${{pairs.length}} paired briefs`,
    `A ${{A}}   B ${{B}}   tie ${{tie}}   can't tell ${{skip}}   unjudged ${{pairs.length - A - B - tie - skip}}`,
    cast ? `agreed with the judge on ${{agree}}/${{cast}} of the pairs I called` : "",
    "",
  ].filter(Boolean);
  document.getElementById("out").value = head.concat(lines).join("\\n");
}}

for (const p of pairs) {{
  for (const b of p.querySelectorAll(".vote button")) {{
    b.addEventListener("click", () => {{
      votes[p.dataset.i] = votes[p.dataset.i] === b.dataset.v ? undefined : b.dataset.v;
      if (!votes[p.dataset.i]) delete votes[p.dataset.i];
      save(); paint();
    }});
  }}
}}

const blindBtn = document.getElementById("blind");
const setBlind = (on) => {{
  document.body.classList.toggle("blind", on);
  blindBtn.classList.toggle("on", on);
  blindBtn.textContent = "blind: " + (on ? "on" : "off");
  paint();
}};
blindBtn.addEventListener("click", () => setBlind(!document.body.classList.contains("blind")));
document.getElementById("reveal").addEventListener("click", () => setBlind(false));
document.getElementById("clear").addEventListener("click", () => {{ votes = {{}}; save(); paint(); }});

const lb = document.getElementById("lb");
for (const s of document.querySelectorAll(".sheet[data-full]")) {{
  s.addEventListener("click", () => {{ lb.querySelector("img").src = s.dataset.full; lb.classList.add("open"); }});
}}
lb.addEventListener("click", () => lb.classList.remove("open"));
addEventListener("keydown", (e) => {{ if (e.key === "Escape") lb.classList.remove("open"); }});

setBlind(true);
</script>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs_dir", type=Path)
    ap.add_argument("--a", required=True, help="slug prefix for arm A")
    ap.add_argument("--b", required=True, help="slug prefix for arm B")
    ap.add_argument("--title", default="A/B round")
    ap.add_argument("--a-label", default="control")
    ap.add_argument("--b-label", default="treatment")
    ap.add_argument("--changed", default="", help="one line: what differs between the arms")
    ap.add_argument("--out", type=Path, required=True)
    ns = ap.parse_args()
    ns.out.write_text(build(ns.runs_dir, ns.a, ns.b, title=ns.title, a_label=ns.a_label,
                            b_label=ns.b_label, changed=ns.changed))
    print(f"{ns.out}  ({ns.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
