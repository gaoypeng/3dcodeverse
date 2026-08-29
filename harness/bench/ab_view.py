#!/usr/bin/env python3
"""Render an A/B experiment round as ONE self-contained page you can look at.

    python bench/ab_view.py bench/out/<battery>/runs --a cb_a --b cb_b \
        --title "urdf cookbook" --a-label "no cookbook" --b-label "cookbook delivered" \
        --out /tmp/ab.html

Every A/B in this repo is decided on numbers, but a score is a summary of an image and
the owner wants the image.  This puts the two arms' contact sheets side by side at a size
you can actually judge, with the run's numbers under each and the honest verdict — including
"inconclusive" — at the top.  Runs are grouped by the slug prefix given to --a / --b.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

SHEET_W = 1100


@dataclass
class Run:
    slug: str
    arm: str
    status: str = "?"
    final: float | None = None
    baseline: float | None = None
    rounds: int = 0
    usd: float = 0.0
    minutes: int = 0
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
    rec = run_dir / "record.json"
    if rec.is_file():
        d = json.loads(rec.read_text())
        b = (d.get("extra") or {}).get("budget") or {}
        r.status = str(d.get("status") or "?")
        r.final, r.baseline = d.get("final_score"), d.get("baseline_score")
        r.rounds = len(d.get("rounds") or [])
        r.usd, r.minutes = round(float(b.get("spent_usd") or 0), 2), round(float(b.get("elapsed_min") or 0))
        r.error = str(d.get("error") or "")[:300]
        for rd in d.get("rounds") or []:
            failed = [g.get("gate") for g in (rd.get("gates") or []) if not g.get("passed")]
            if failed:
                r.notes.append(f"r{rd.get('index')} gates: {', '.join(str(g) for g in failed[:4])}")
    r.sheet = _sheet_data_uri(run_dir)
    return r


#: scored runs per arm below which this function will not name a winner, whatever the
#: numbers say.  A reference harness on the same model tier measured a run-to-run score
#: sd of 0.159 against a re-judge sd of 0.013, giving a minimum detectable effect of
#: 0.081 at n=30 — and it published, then retracted, several prompt findings because a
#: promising first replicate turned out to be noise pointing the right way
#: (interpenetration −0.40 in replicate 1, +0.03 in replicate 2, pooled p=0.405).
MIN_RUNS_PER_ARM = 3


def verdict(a: list[Run], b: list[Run]) -> tuple[str, str]:
    """(headline, why) — refuses to call a winner the sample cannot support."""
    sa = [x.final for x in a if x.final is not None]
    sb = [x.final for x in b if x.final is not None]
    if len(sa) < MIN_RUNS_PER_ARM or len(sb) < MIN_RUNS_PER_ARM:
        return ("Inconclusive — not enough scored runs",
                f"arm A scored {len(sa)} of {len(a)} runs, arm B {len(sb)} of {len(b)}; this needs "
                f"at least {MIN_RUNS_PER_ARM} per arm before a winner is named. A run that died "
                "before it scored is not an observation about the arm — it is a missing one.")
    ma, mb = statistics.mean(sa), statistics.mean(sb)
    # WITHIN-arm spread: how much runs of the SAME arm disagree.  Pooling both arms would
    # fold the effect being measured into the yardstick and flatter any real difference.
    within = max(statistics.pstdev(sa), statistics.pstdev(sb), 1e-9)
    if abs(mb - ma) < within:
        return (f"Inconclusive — Δ {mb - ma:+.3f} is inside the within-arm spread ({within:.3f})",
                f"A {ma:.3f} (n={len(sa)}) vs B {mb:.3f} (n={len(sb)}). Runs of the same arm differ "
                "by more than the arms differ from each other, so this is not evidence.")
    return (f"B {'wins' if mb > ma else 'loses'} by {mb - ma:+.3f}",
            f"A {ma:.3f} (n={len(sa)}) vs B {mb:.3f} (n={len(sb)}); within-arm spread {within:.3f}. "
            "Worth a confirming replicate before it is believed.")


def _num(v: object) -> str:
    return "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def _card(r: Run) -> str:
    tone = {"passed": "good", "failed": "bad", "budget": "warn"}.get(r.status, "warn")
    img = (f'<div class="sheet"><img src="{r.sheet}" alt="rendered views for {html.escape(r.slug)}" loading="lazy"></div>'
           if r.sheet else '<div class="sheet empty">no render — the run died before anything was built</div>')
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in r.notes)
    err = f'<p class="err">{html.escape(r.error)}</p>' if r.error else ""
    return f"""<article class="run">
  <header><h4>{html.escape(r.slug)}</h4><span class="pill {tone}">{html.escape(r.status)}</span></header>
  {img}
  <dl>
    <div><dt>baseline</dt><dd>{_num(r.baseline)}</dd></div>
    <div><dt>final</dt><dd class="lead">{_num(r.final)}</dd></div>
    <div><dt>rounds</dt><dd>{r.rounds}</dd></div>
    <div><dt>cost</dt><dd>${r.usd:.2f}</dd></div>
    <div><dt>wall</dt><dd>{r.minutes} min</dd></div>
  </dl>
  {f'<ul class="notes">{notes}</ul>' if notes else ''}{err}
</article>"""


def build(runs_dir: Path, a_prefix: str, b_prefix: str, *, title: str,
          a_label: str, b_label: str, changed: str) -> str:
    a = [load_run(d, "A") for d in sorted(runs_dir.iterdir()) if d.is_dir() and d.name.startswith(a_prefix)]
    b = [load_run(d, "B") for d in sorted(runs_dir.iterdir()) if d.is_dir() and d.name.startswith(b_prefix)]
    head, why = verdict(a, b)
    return TEMPLATE.format(
        title=html.escape(title), head=html.escape(head), why=html.escape(why),
        changed=html.escape(changed), a_label=html.escape(a_label), b_label=html.escape(b_label),
        n_a=len(a), n_b=len(b),
        a_cards="\n".join(_card(r) for r in a), b_cards="\n".join(_card(r) for r in b))


TEMPLATE = """<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --ground:#f4f5f2; --panel:#fbfbf9; --line:#d9dcd4; --ink:#1b1f1a; --dim:#5d6459;
  --accent:#2f6b74; --good:#3f6b46; --warn:#8a6520; --bad:#8c4032; --shadow:0 1px 2px rgba(27,31,26,.06);
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --ground:#12150f; --panel:#191d16; --line:#2c3128; --ink:#e8eae3; --dim:#9aa294;
  --accent:#6fb3bd; --good:#7aab81; --warn:#c9a052; --bad:#cf7f6e; --shadow:none;
}} }}
:root[data-theme="dark"] {{
  --ground:#12150f; --panel:#191d16; --line:#2c3128; --ink:#e8eae3; --dim:#9aa294;
  --accent:#6fb3bd; --good:#7aab81; --warn:#c9a052; --bad:#cf7f6e; --shadow:none;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);
  font:16px/1.6 "IBM Plex Sans",ui-sans-serif,system-ui,sans-serif;}}
.wrap{{max-width:1500px;margin:0 auto;padding:40px 28px 72px;display:flex;flex-direction:column;gap:30px}}
h1{{font-size:1.6rem;font-weight:600;margin:0;letter-spacing:-.01em;text-wrap:balance}}
.eyebrow{{font:500 .72rem/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.13em;
  text-transform:uppercase;color:var(--accent);margin:0 0 10px}}
.verdict{{border:1px solid var(--line);border-left:3px solid var(--accent);background:var(--panel);
  border-radius:3px;padding:18px 22px;box-shadow:var(--shadow)}}
.verdict h2{{font-size:1.05rem;margin:0 0 6px;font-weight:600}}
.verdict p{{margin:0;color:var(--dim);max-width:74ch}}
.changed{{font:400 .88rem/1.65 "IBM Plex Mono",ui-monospace,monospace;color:var(--dim);
  background:var(--panel);border:1px solid var(--line);border-radius:3px;padding:14px 18px;
  overflow-x:auto;max-width:100%}}
.arms{{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:26px;align-items:start}}
.arm > h3{{font-size:.95rem;font-weight:600;margin:0 0 4px;display:flex;gap:10px;align-items:baseline}}
.arm > p{{margin:0 0 16px;color:var(--dim);font-size:.88rem}}
.tag{{font:500 .68rem/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.1em;padding:4px 7px;
  border:1px solid var(--line);border-radius:2px;color:var(--dim);text-transform:uppercase}}
.run{{background:var(--panel);border:1px solid var(--line);border-radius:3px;margin-bottom:20px;
  overflow:hidden;box-shadow:var(--shadow)}}
.run header{{display:flex;justify-content:space-between;align-items:center;gap:12px;
  padding:11px 15px;border-bottom:1px solid var(--line)}}
.run h4{{margin:0;font:500 .9rem/1 "IBM Plex Mono",ui-monospace,monospace}}
.pill{{font:500 .68rem/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.06em;
  padding:4px 8px;border-radius:2px;text-transform:uppercase;color:var(--panel)}}
.pill.good{{background:var(--good)}} .pill.warn{{background:var(--warn)}} .pill.bad{{background:var(--bad)}}
.sheet{{overflow-x:auto;background:var(--ground);border-bottom:1px solid var(--line)}}
.sheet img{{display:block;width:100%;height:auto}}
.sheet.empty{{padding:44px 18px;text-align:center;color:var(--dim);font-size:.88rem;border-bottom:1px solid var(--line)}}
dl{{display:flex;flex-wrap:wrap;gap:0;margin:0;padding:12px 15px}}
dl > div{{flex:1 1 78px;border-right:1px solid var(--line);padding-right:12px;margin-right:12px}}
dl > div:last-child{{border-right:0}}
dt{{font:500 .66rem/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.09em;
  text-transform:uppercase;color:var(--dim);margin-bottom:5px}}
dd{{margin:0;font:500 .98rem/1 "IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}}
dd.lead{{font-size:1.2rem;color:var(--accent)}}
.notes,.err{{margin:0;padding:0 15px 13px;color:var(--dim);font-size:.83rem}}
.notes{{list-style:none}} .notes li:before{{content:"› ";color:var(--accent)}}
.err{{font-family:"IBM Plex Mono",ui-monospace,monospace;color:var(--bad);word-break:break-word}}
</style>
<div class="wrap">
  <div>
    <p class="eyebrow">A/B round</p>
    <h1>{title}</h1>
  </div>
  <div class="verdict">
    <h2>{head}</h2>
    <p>{why}</p>
  </div>
  <div class="changed">{changed}</div>
  <div class="arms">
    <section class="arm">
      <h3>Arm A <span class="tag">{n_a} runs</span></h3>
      <p>{a_label}</p>
      {a_cards}
    </section>
    <section class="arm">
      <h3>Arm B <span class="tag">{n_b} runs</span></h3>
      <p>{b_label}</p>
      {b_cards}
    </section>
  </div>
</div>
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
