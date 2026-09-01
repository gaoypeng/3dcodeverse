"""Objects head-to-head: this harness vs the astra3d-brilliana curated gallery.

Protocol (one fixed judge, one renderer, one gate set, for BOTH sides):

1. A battery ``bench/prompts/h2h_brilliana_v1_<lang>.yaml`` carries THEIR prompts
   verbatim with an empty ``must_have`` (their run had no checklist, so ours gets none);
   each prompt's last tag is the gallery slug.
2. THEIR GLB is ``<gallery>/<slug>/object.glb`` (built by their harness, never
   rebuilt here).  OUR GLB is the best round of the harness run under
   ``<bench_out>/<lang>/runs/<id>/`` (``deliverable/object.glb``, else
   ``artifacts/object.glb`` — ``finalise`` restores and rebuilds the best round).
3. Both GLBs go through the same pipeline as ``bench/_fixed_eval.FixedEvaluator``:
   ``measure_glb`` → ``check_connectivity`` → ``render_glb`` (OBJECT_VIEWS, settings
   size, contact sheet) → ``VlmJudge(static_object_v1, gemini-3.1-pro-preview,
   n_samples=2)`` with a ``Spec`` built from the prompt and an EMPTY acceptance list.
   The judge is called directly on a ``JudgeInput`` because their side has no
   workspace to build from.  Every judged side is cached as
   ``<out>/eval/<id>/<side>.json`` so a re-run never re-buys a verdict.
4. Outputs: ``<out>/h2h.jsonl`` (one row per prompt), ``<out>/pairs/<id>.png``
   (their sheet | our sheet) and ``<out>/h2h_summary.md`` (paired mean delta, sd,
   exact two-sided sign test, per-language rows).  The gallery's own score is kept
   in the row for reference only — it came from a different judge and is never
   compared with ours.

Usage::

    python bench/h2h_glb.py bench/prompts/h2h_brilliana_v1_{blender,cadquery,threejs}.yaml \
        --gallery /home/yipeng/projects/astra3d-brilliana/gallery --bench-out bench/out/h2h_brilliana_v1
    python bench/h2h_glb.py <battery.yaml> --theirs-only --id h2h_c_clamp_mv    # prove the judge path
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `python bench/h2h_glb.py` from the repo root

from bench._fixed_eval import RUBRIC  # noqa: E402
from bench.h2h_scene import sign_test  # noqa: E402
from bench.run_bench import Battery, BenchPrompt  # noqa: E402

JUDGE_MODEL = "gemini:gemini-3.1-pro-preview"
N_SAMPLES = 2
TERMINAL = {"passed", "plateau", "budget", "failed"}  # RunStatus values a finished run can hold
_H2H_TAGS = {"h2h", "brilliana"}


class Side(BaseModel):
    """One judged GLB (theirs or ours) — everything the row needs, nothing more."""

    source: str
    glb: str = ""
    score: float | None = None
    score_std: float = 0.0
    visual_score: float | None = None   # the same judge with NO gate findings in its context: perception only
    passed: bool | None = None
    gate_errors: list[str] = Field(default_factory=list)
    floating_parts: int = 0
    tris: int | None = None
    parts: int | None = None
    ground_gap_m: float | None = None
    sheet: str = ""
    summary: str = ""
    error: str = ""
    rounds: int | None = None          # harness side only
    cost_usd: float | None = None
    minutes: float | None = None
    status: str = ""
    gallery_score: float | None = None  # their own judge; reference only


class Row(BaseModel):
    id: str
    slug: str
    language: str
    prompt: str
    theirs: Side
    ours: Side
    delta: float | None = None
    delta_visual: float | None = None
    side_by_side: str = ""


# ----------------------------------------------------------------------------- locate
def slug_of(item: BenchPrompt) -> str:
    slugs = [t for t in item.tags if t not in _H2H_TAGS]
    if not slugs:
        raise ValueError(f"{item.id}: no gallery slug tag")
    return slugs[-1]


def their_side(gallery: Path, slug: str) -> Side:
    meta = json.loads((gallery / slug / "meta.json").read_text())
    glb = gallery / slug / "object.glb"
    return Side(source="brilliana", glb=str(glb), gallery_score=meta.get("score"),
                error="" if glb.is_file() else f"missing {glb}")


def our_side(runs_dir: Path, item_id: str) -> Side:
    ws = runs_dir / item_id
    rec_path = ws / "record.json"
    if not rec_path.is_file():
        return Side(source="3dcodeverse", status="not_run", error=f"no record.json under {ws}")
    rec: dict[str, Any] = json.loads(rec_path.read_text())
    status = str(rec.get("status", ""))
    side = Side(source="3dcodeverse", status=status, rounds=len(rec.get("rounds", [])),
                cost_usd=float((rec.get("total_usage") or {}).get("cost_usd", 0.0)))
    try:
        t0 = datetime.fromisoformat(rec["started_at"])
        t1 = datetime.fromisoformat(rec["finished_at"])
        side.minutes = round((t1 - t0).total_seconds() / 60, 2)
    except (KeyError, TypeError, ValueError):
        pass
    if status not in TERMINAL or rec.get("best_round") is None:
        side.error = f"run not finished (status={status!r}, best_round={rec.get('best_round')})"
        return side
    for cand in (ws / "deliverable" / "object.glb", ws / "artifacts" / "object.glb"):
        if cand.is_file():
            side.glb = str(cand)
            return side
    side.error = f"finished run has no object.glb under {ws}"
    return side


# ----------------------------------------------------------------------------- evaluate
def build_spec(battery: Battery, item: BenchPrompt) -> Any:
    from codeverse.contracts.spec import Spec

    return Spec(id=f"h2h/{item.id}", track=battery.track, language=item.language or battery.language,
                prompt=item.prompt, tags=["h2h", *item.tags])


def evaluate(side: Side, spec: Any, out_dir: Path, judge: Any) -> Side:
    """measure → connectivity → render → judge, exactly the FixedEvaluator order."""
    from codeverse.config import get_settings
    from codeverse.conventions import OBJECT_VIEWS
    from codeverse.judges.base import JudgeInput
    from codeverse.spatial.connectivity import check_connectivity
    from codeverse.spatial.measure import measure_glb
    from codeverse.spatial.render import render_glb

    if side.error or not side.glb:
        return side
    try:
        glb = Path(side.glb)
        m = measure_glb(glb)
        side.tris, side.parts, side.ground_gap_m = m.tri_count, m.n_meshes, round(m.ground_gap_m, 4)
        gate = check_connectivity(glb, language=str(spec.language))
        side.gate_errors = [f"{gate.gate}: {f.message}" for f in gate.errors]
        side.floating_parts = sum("is floating" in f.message for f in gate.errors)
        r = get_settings().render
        renders = render_glb(glb, out_dir / "renders", views=list(OBJECT_VIEWS), width=r.width, height=r.height, sheet=True)
        side.sheet = renders.contact_sheet or ""
        j = judge.judge(JudgeInput(spec=spec, renders=renders, measurement=m, gates=[gate], acceptance=[], round_index=0))
        side.score, side.score_std, side.passed, side.summary = j.overall, j.score_std, j.passed, j.summary
        (out_dir / "judgment.json").write_text(j.model_dump_json(indent=2))
        # Perception only: the gallery GLBs are merged, un-welded exports whose "floating"
        # findings are export artefacts as often as defects (a microscope whose stage
        # sits 22 mm from its arm in the mesh but looks attached), so a judge that reads
        # the gate text in its context caps them for what the eye cannot see.  The
        # visual number is the fair headline; the gated one is what the harness would say.
        jv = judge.judge(JudgeInput(spec=spec, renders=renders, measurement=m, gates=[], acceptance=[], round_index=0))
        side.visual_score = jv.overall
        (out_dir / "judgment_visual.json").write_text(jv.model_dump_json(indent=2))
    except Exception as e:  # noqa: BLE001 — one bad GLB must not kill the battery
        side.error = f"{type(e).__name__}: {e}"
    return side


def cached_eval(side: Side, spec: Any, out_dir: Path, judge: Any, *, force: bool) -> Side:
    cache = out_dir / f"{side.source}.json"
    if cache.is_file() and not force:
        prev = Side.model_validate_json(cache.read_text())
        if prev.score is not None and prev.visual_score is not None and prev.glb == side.glb:
            return prev.model_copy(update={k: getattr(side, k) for k in ("rounds", "cost_usd", "minutes", "status")})
    out_dir.mkdir(parents=True, exist_ok=True)
    side = evaluate(side, spec, out_dir, judge)
    cache.write_text(side.model_dump_json(indent=2))
    return side


def side_by_side(theirs: Side, ours: Side, dst: Path, slug: str) -> str:
    from PIL import Image, ImageDraw

    tiles = [(f"THEIRS  brilliana/{slug}", theirs.sheet, theirs.score), (f"OURS  3dcodeverse/{slug}", ours.sheet, ours.score)]
    imgs = [Image.open(p).convert("RGB") if p and Path(p).is_file() else Image.new("RGB", (768, 768), (40, 40, 40)) for _, p, _ in tiles]
    h = min(i.height for i in imgs)
    imgs = [i.resize((int(i.width * h / i.height), h)) for i in imgs]
    band, gap = 36, 12
    canvas = Image.new("RGB", (sum(i.width for i in imgs) + gap, h + band), (20, 20, 20))
    draw, x = ImageDraw.Draw(canvas), 0
    for (label, _, score), im in zip(tiles, imgs, strict=True):
        canvas.paste(im, (x, band))
        draw.text((x + 8, 10), f"{label}   score={score if score is None else f'{score:.3f}'}", fill=(255, 255, 255))
        x += im.width + gap
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst)
    return str(dst)


# ----------------------------------------------------------------------------- stats
def _stats(rows: list[Row], *, visual: bool = False) -> str:
    d = [(r.delta_visual if visual else r.delta) for r in rows if (r.delta_visual if visual else r.delta) is not None]
    if not d:
        return "n=0 (no pair judged on both sides)"
    sd = statistics.stdev(d) if len(d) > 1 else 0.0
    wins, losses = sum(x > 0 for x in d), sum(x < 0 for x in d)
    n_nz, _, p = sign_test(d)
    return (f"n={len(d)}  mean Δ(ours−theirs)={statistics.mean(d):+.3f}  sd={sd:.3f}  "
            f"wins/losses/ties={wins}/{losses}/{len(d) - wins - losses}  sign-test p={'n/a' if n_nz == 0 else f'{p:.3f}'}")


def summary_md(rows: list[Row]) -> str:
    out = ["# Objects head-to-head: 3dcodeverse vs astra3d-brilliana gallery", "",
           f"Judge: `{JUDGE_MODEL}` × {N_SAMPLES} samples, rubric `{RUBRIC}`, empty acceptance list, "
           "same renderer / views / connectivity gate on both GLBs.", "",
           f"**All languages, visual only (no gate text in the judge's context — the fair headline)**: {_stats(rows, visual=True)}",
           f"**All languages, gated (what the harness itself would say)**: {_stats(rows)}", ""]
    for lang in sorted({r.language for r in rows}):
        out.append(f"- **{lang}** visual: {_stats([r for r in rows if r.language == lang], visual=True)} · gated: {_stats([r for r in rows if r.language == lang])}")
    out += ["", "| id | lang | theirs visual | ours visual | Δ visual | theirs gated | ours gated | Δ gated | theirs gate errs / floating | ours gate errs / floating | ours rounds / $ / min | their gallery score (ref only) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    fmt = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    for r in rows:
        t, o = r.theirs, r.ours
        out.append(f"| {r.id} | {r.language} | {fmt(t.visual_score)} | {fmt(o.visual_score)} | {'—' if r.delta_visual is None else f'{r.delta_visual:+.3f}'} "
                   f"| {fmt(t.score)} | {fmt(o.score)} | {'—' if r.delta is None else f'{r.delta:+.3f}'} "
                   f"| {len(t.gate_errors)} / {t.floating_parts} | {len(o.gate_errors)} / {o.floating_parts} "
                   f"| {o.rounds or '—'} / {'—' if o.cost_usd is None else f'{o.cost_usd:.2f}'} / {'—' if o.minutes is None else f'{o.minutes:.0f}'} "
                   f"| {fmt(t.gallery_score)} |")
    errs = [f"- {r.id} {s.source}: {s.error}" for r in rows for s in (r.theirs, r.ours) if s.error]
    if errs:
        out += ["", "## Unjudged sides", *errs]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- main
def run(batteries: list[Path], gallery: Path, bench_out: Path, out: Path, *, ids: set[str], theirs_only: bool, force: bool) -> list[Row]:
    from codeverse.cost import run_ledger
    from codeverse.judges.vlm_judge import VlmJudge

    judge = VlmJudge(rubric=RUBRIC, model_id=JUDGE_MODEL, n_samples=N_SAMPLES)
    rows: list[Row] = []
    out.mkdir(parents=True, exist_ok=True)
    with run_ledger(out, run="h2h_brilliana_v1"):
        for bpath in batteries:
            battery = Battery.load(bpath)
            runs_dir = bench_out / str(battery.language) / "runs"
            for item in battery.prompts:
                if ids and item.id not in ids:
                    continue
                slug, spec, edir = slug_of(item), build_spec(battery, item), out / "eval" / item.id
                theirs = cached_eval(their_side(gallery, slug), spec, edir / "theirs", judge, force=force)
                ours = Side(source="3dcodeverse", status="skipped") if theirs_only else \
                    cached_eval(our_side(runs_dir, item.id), spec, edir / "ours", judge, force=force)
                row = Row(id=item.id, slug=slug, language=str(spec.language), prompt=item.prompt, theirs=theirs, ours=ours)
                if theirs.score is not None and ours.score is not None:
                    row.delta = round(ours.score - theirs.score, 4)
                if ours.visual_score is not None and theirs.visual_score is not None:
                    row.delta_visual = round(ours.visual_score - theirs.visual_score, 4)
                if theirs.sheet or ours.sheet:
                    row.side_by_side = side_by_side(theirs, ours, out / "pairs" / f"{item.id}.png", slug)
                rows.append(row)
                print(f"{item.id:<24} theirs={theirs.score} ours={ours.score} Δ={row.delta}  "
                      f"gates t/o={len(theirs.gate_errors)}/{len(ours.gate_errors)}  {theirs.error or ours.error}")
    with (out / "h2h.jsonl").open("w") as fh:
        for r in rows:
            fh.write(r.model_dump_json() + "\n")
    (out / "h2h_summary.md").write_text(summary_md(rows))
    print(summary_md(rows))
    return rows


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("batteries", nargs="+", type=Path)
    ap.add_argument("--gallery", type=Path, default=Path("/home/yipeng/projects/astra3d-brilliana/gallery"))
    ap.add_argument("--bench-out", type=Path, default=here / "out" / "h2h_brilliana_v1",
                    help="root holding <lang>/runs/<id>/ (one `3dcv bench run --out <root>/<lang>` per battery)")
    ap.add_argument("--out", type=Path, default=None, help="where h2h.jsonl / pairs/ / h2h_summary.md go (default: --bench-out)")
    ap.add_argument("--id", action="append", default=[], help="only these prompt ids")
    ap.add_argument("--theirs-only", action="store_true", help="judge only the gallery GLBs (proof of the judge path)")
    ap.add_argument("--force", action="store_true", help="ignore cached verdicts under <out>/eval/")
    a = ap.parse_args()
    run(a.batteries, a.gallery, a.bench_out, a.out or a.bench_out, ids=set(a.id), theirs_only=a.theirs_only, force=a.force)


if __name__ == "__main__":
    main()
