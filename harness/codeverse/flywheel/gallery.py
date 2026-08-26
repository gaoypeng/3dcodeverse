"""Self-contained HTML gallery of runs: one card per run with the best round's
contact sheet (embedded thumbnail), score / cost / rounds / tier, prompt,
track · language · backend and ``file://`` links to the artifacts.

``render_gallery(items, title)`` is the shared renderer: ``3dcv flywheel gallery``
feeds it run records (``gallery_items``), ``bench/report.py`` feeds it bench
results.  No external assets — images are base64 JPEG thumbnails, the filter /
sort controls are a few lines of inline JS.
"""

from __future__ import annotations

import base64
import html
import io
import statistics
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.contracts.run import RunRecord
from codeverse.flywheel.deliverable import load_deliverable
from codeverse.flywheel.quality import quality_tier
from codeverse.flywheel.record import effective_judgment, iter_runs
from codeverse.flywheel.sample import best_round_record, gate_error_summary, telemetry_digest
from codeverse.workspace import Workspace

THUMB_PX = 640
JPEG_QUALITY = 78


class GalleryItem(BaseModel):
    key: str
    title: str = ""
    prompt: str = ""
    track: str = ""
    language: str = ""
    generator: str = ""
    judge: str = ""
    score: float | None = None
    baseline_score: float | None = None
    passed: bool | None = None
    quality_tier: str = "D"
    gate_errors: int = 0
    cost_usd: float = 0.0
    rounds: int = 0
    best_round: int | None = None
    minutes: float | None = None
    status: str = ""
    error: str = ""
    group: str = Field(default="", description="bench tier / category label shown on the card")
    caption: str = Field(default="", description="instruction caption when available")
    cost_by_stage: dict[str, float] = Field(default_factory=dict, description="stage → USD (telemetry, when present)")
    models: dict[str, str] = Field(default_factory=dict, description="role → model id (telemetry, when present)")
    sheet: str | None = Field(default=None, description="contact sheet path (best round)")
    links: dict[str, str] = Field(default_factory=dict, description="label → absolute path")


def item_from_run(ws: Workspace, rec: RunRecord) -> GalleryItem:
    rnd = best_round_record(rec)
    j = effective_judgment(rnd) if rnd is not None else None  # degraded → unjudged
    gates = gate_error_summary(rnd)
    n_err = sum(gates.values())
    sheet = None
    if rnd is not None and rnd.renders is not None and rnd.renders.contact_sheet:
        p = Path(rnd.renders.contact_sheet)
        p = p if p.is_absolute() else ws.root / p
        sheet = str(p) if p.is_file() else None
    if sheet is None and (ws.deliverable / "sheet.png").is_file():
        sheet = str(ws.deliverable / "sheet.png")  # new layout: the packaged best sheet
    links = {"workspace": str(ws.root), "record.json": str(ws.record_path)}
    if sheet:
        links["sheet"] = sheet
    deliverable = load_deliverable(ws, rec)
    if deliverable is not None and ws.deliverable.is_dir():
        links["deliverable/"] = str(ws.deliverable)
    for name in ("object.glb", "robot.urdf", "object.stl"):
        path = next((d / name for d in (ws.deliverable, ws.artifacts) if (d / name).is_file()), None)
        if path is not None:
            links[name] = str(path)
    if (ws.root / "src").is_dir():
        links["src/"] = str(ws.root / "src")
    if ws.cost_path.is_file():
        links["cost.json"] = str(ws.cost_path)
    digest = telemetry_digest(ws, rec)
    minutes = ((rec.finished_at - rec.started_at).total_seconds() / 60.0) if rec.finished_at else None
    caps = rec.extra.get("captions") or {}
    return GalleryItem(
        key=ws.root.name,
        title=(getattr(rec.plan, "object_name", "") or getattr(rec.plan, "title", "") or "") if rec.plan else "",
        prompt=rec.spec.prompt, track=rec.spec.track.value, language=rec.spec.language.value,
        generator=rec.spec.backends.generator, judge=rec.spec.backends.judge,
        score=j.overall if j else rec.final_score, baseline_score=rec.baseline_score,
        passed=j.passed if j else None,
        quality_tier=quality_tier(passed=j.passed if j else None, gate_errors=n_err, score=j.overall if j else None),
        gate_errors=n_err, cost_usd=rec.total_usage.cost_usd, rounds=len(rec.rounds), best_round=rec.best_round,
        minutes=round(minutes, 1) if minutes is not None else None, status=rec.status.value, error=rec.error,
        caption=str(caps.get("instruction", "") or ""),
        cost_by_stage={k: round(v, 4) for k, v in (digest.get("by_stage") or {}).items()},
        models=dict(digest.get("models") or {}),
        sheet=sheet, links=links,
    )


def gallery_items(runs_dir: Path | str) -> list[GalleryItem]:
    items: list[GalleryItem] = []
    for ws, rec in iter_runs(runs_dir, on_error=lambda d, e: items.append(
            GalleryItem(key=d.name, status="invalid", error=str(e), links={"workspace": str(d)}))):
        items.append(item_from_run(ws, rec))
    return items


# --------------------------------------------------------------------------- rendering
def thumbnail_data_uri(path: Path | str, *, max_px: int = THUMB_PX) -> str:
    """Downscaled JPEG as a ``data:`` URI (``""`` when the image cannot be read)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception:  # missing PIL, unreadable file, …
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _fmt(v: float | None, spec: str = ".3f") -> str:
    return "-" if v is None else format(v, spec)


def _file_href(path: str) -> str:
    p = Path(path)
    return p.as_uri() if p.is_absolute() else html.escape(path)


def _card(it: GalleryItem, *, thumb_px: int) -> str:
    img = thumbnail_data_uri(it.sheet, max_px=thumb_px) if it.sheet else ""
    pic = f'<a href="{_file_href(it.sheet)}"><img src="{img}" alt="contact sheet" loading="lazy"></a>' if img else \
        '<div class="noimg">no render</div>'
    badge = "pass" if it.passed else ("fail" if it.passed is False else "na")
    links = " · ".join(f'<a href="{_file_href(p)}">{html.escape(n)}</a>' for n, p in it.links.items())
    head = html.escape(it.title or it.key)
    grp = f' · <span class="grp">{html.escape(it.group)}</span>' if it.group else ""
    err = f'<div class="err">{html.escape(it.error[:300])}</div>' if it.error else ""
    cap = f'<div class="cap">{html.escape(it.caption)}</div>' if it.caption else ""
    mins = f" · {it.minutes:.1f} min" if it.minutes is not None else ""
    stage_costs = " · ".join(f"{html.escape(k)} ${v:.3f}"
                             for k, v in sorted(it.cost_by_stage.items(), key=lambda kv: -kv[1]))
    stages = f'<div class="kv">cost: {stage_costs}</div>' if stage_costs else ""
    return (
        f'<div class="card {badge}" data-track="{html.escape(it.track)}" data-tier="{it.quality_tier}" '
        f'data-score="{it.score if it.score is not None else -1}" data-cost="{it.cost_usd}" data-key="{html.escape(it.key)}">'
        f'{pic}<div class="meta">'
        f'<div class="row1"><b>{head}</b> <span class="tier t{it.quality_tier}">{it.quality_tier}</span>'
        f'<span class="score">{_fmt(it.score)}</span></div>'
        f'<div class="prompt">{html.escape(it.prompt)}</div>{cap}'
        f'<div class="kv">{html.escape(it.track)} · {html.escape(it.language)} · {html.escape(it.generator)}{grp}</div>'
        f'<div class="kv">baseline {_fmt(it.baseline_score)} → best {_fmt(it.score)} (r{it.best_round if it.best_round is not None else "-"}) · '
        f'rounds {it.rounds} · gate err {it.gate_errors} · ${it.cost_usd:.2f}{mins} · {html.escape(it.status)}</div>'
        f'{stages}{err}<div class="links">{links}</div></div></div>'
    )


def _summary(items: list[GalleryItem]) -> str:
    scored = [i.score for i in items if i.score is not None]
    judged = [i.passed for i in items if i.passed is not None]
    tiers = {t: sum(1 for i in items if i.quality_tier == t) for t in "ABCD"}
    parts = [
        f"{len(items)} runs",
        f"pass {sum(judged)}/{len(judged)}" if judged else "pass -",
        f"mean score {statistics.fmean(scored):.3f}" if scored else "mean score -",
        f"total ${sum(i.cost_usd for i in items):.2f}",
        "tiers " + " ".join(f"{t}:{n}" for t, n in tiers.items()),
    ]
    return " · ".join(html.escape(p) for p in parts)


_STYLE = """
body{font-family:system-ui,sans-serif;margin:20px;background:#111;color:#eee}
h1{font-size:20px;margin:0 0 6px}.sum{color:#bbb;margin-bottom:10px}
.ctl{margin:8px 0 14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:13px}
.ctl select,.ctl input{background:#222;color:#eee;border:1px solid #444;padding:3px 6px;border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:14px}
.card{background:#1c1c1c;border-radius:8px;overflow:hidden;border:2px solid #333;display:flex;flex-direction:column}
.card.pass{border-color:#2e7d32}.card.fail{border-color:#8a1c1c}.card img{width:100%;max-height:420px;object-fit:contain;display:block;background:#fff}
.noimg{height:160px;display:flex;align-items:center;justify-content:center;color:#777}
.meta{padding:8px 10px;font-size:13px;line-height:1.4}.row1{display:flex;gap:8px;align-items:center}
.score{margin-left:auto;font-weight:600;font-size:15px}.tier{padding:0 6px;border-radius:4px;font-weight:700;font-size:12px}
.tA{background:#2e7d32}.tB{background:#558b2f}.tC{background:#b07a00}.tD{background:#8a1c1c}
.prompt{color:#ddd;margin:4px 0}.cap{color:#9bd;font-style:italic;margin:2px 0 4px}.kv{color:#aaa}.grp{color:#cba}
.err{color:#f88;white-space:pre-wrap;margin-top:4px}.links{margin-top:6px}.links a{color:#8cf;margin-right:4px}
.hidden{display:none}
"""

_SCRIPT = """
function apply(){const t=document.getElementById('ftrack').value,q=document.getElementById('ftier').value,
s=document.getElementById('sort').value,g=document.getElementById('grid'),cards=[...g.children];
cards.forEach(c=>{const ok=(t===''||c.dataset.track===t)&&(q===''||c.dataset.tier===q);c.classList.toggle('hidden',!ok);});
const key={score:c=>-parseFloat(c.dataset.score),cost:c=>-parseFloat(c.dataset.cost),key:c=>c.dataset.key,
tier:c=>c.dataset.tier+(1-parseFloat(c.dataset.score))}[s];
cards.sort((a,b)=>{const x=key(a),y=key(b);return x<y?-1:x>y?1:0}).forEach(c=>g.appendChild(c));}
document.querySelectorAll('#ftrack,#ftier,#sort').forEach(e=>e.addEventListener('change',apply));
"""


def render_gallery(items: list[GalleryItem], title: str, *, thumb_px: int = THUMB_PX, extra_html: str = "") -> str:
    """Complete HTML document (images embedded) for ``items``.  ``extra_html`` is
    inserted above the grid (bench report tables, for example)."""
    tracks = sorted({i.track for i in items if i.track})
    opts = "".join(f'<option value="{html.escape(t)}">{html.escape(t)}</option>' for t in tracks)
    cards = "".join(_card(it, thumb_px=thumb_px) for it in items)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body><h1>{html.escape(title)}</h1>"
        f"<div class='sum'>{_summary(items)}</div>{extra_html}"
        f"<div class='ctl'>track <select id='ftrack'><option value=''>all</option>{opts}</select>"
        f"tier <select id='ftier'><option value=''>all</option>"
        + "".join(f"<option>{t}</option>" for t in "ABCD")
        + "</select>sort <select id='sort'><option value='score'>score</option><option value='tier'>tier</option>"
        f"<option value='cost'>cost</option><option value='key'>name</option></select></div>"
        f"<div class='grid' id='grid'>{cards}</div><script>{_SCRIPT}</script></body></html>"
    )


def write_gallery(runs_dir: Path | str, out_html: Path | str, *, title: str | None = None,
                  thumb_px: int = THUMB_PX) -> tuple[Path, int]:
    """Render every run under ``runs_dir`` into ``out_html``; returns (path, n_items)."""
    items = gallery_items(runs_dir)
    items.sort(key=lambda i: (-(i.score if i.score is not None else -1.0), i.key))
    out = Path(out_html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_gallery(items, title or f"3dcv gallery — {Path(runs_dir).name}", thumb_px=thumb_px))
    return out, len(items)
