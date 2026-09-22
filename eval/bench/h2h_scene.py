"""Head-to-head SCENE judge: this harness's ``scene`` track vs the owner's previous
harnesses (scene_multifile / scene_multifile_graphics), one fixed judge for both sides.

Protocol
--------
* Prompts: ``bench/prompts/h2h_scene_v1.yaml`` — each prompt is the other harness's
  ``outputs/<name>/prompts/input.md`` verbatim, ``must_have`` empty (no checklist either
  side never saw).  Tags ``[h2h, <source_repo>, <name>]`` bind a row to its origin.
* THEIRS: the delivered renders at ``app/artifacts/best_parts/_assembly/object_render/``
  — authored cameras (``view_cam_*``) first, then the overview rig (``view_overview_*``),
  capped at ``MAX_VIEWS``; zone/probe frames are never shown.  Their own judge score,
  wall time and cost come from ``run_report.json`` / ``trajectories/*/score.json``.
* OURS: the best round of ``bench/out/h2h_scene_v1/runs/<id>/`` (``record.best_round`` →
  ``rounds/rNN.json`` renders): the ``t=0`` views the scene judge saw (per-view ``judge``
  flag, authored cameras first), capped at ``MAX_VIEWS`` — stills only, so both sides are
  judged blind to animation in the same way.
* Judge: ONE ``VlmJudge(rubric=scene_v1, gemini-3.1-pro-preview, n_samples=2)`` fed a
  ``JudgeInput(spec, RenderSet)`` with no gates, no measurement, no acceptance items and
  the same ``extra_context`` for both sides.  Verdicts are cached per (id, side) under
  ``out/judge/`` so re-runs never re-pay.
* Output: ``h2h.jsonl`` (one row per prompt), ``sheets/<id>.png`` (their strip over our
  strip), ``h2h_summary.md`` (paired delta mean/sd, exact sign test, time + cost).

Usage: ``python bench/h2h_scene.py [--ids a,b] [--theirs-only] [--n-samples 2]``
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench.run_bench import Battery, BenchPrompt  # noqa: E402
from codeverse3d.contracts.artifacts import RenderSet, RenderView  # noqa: E402
from codeverse3d.contracts.common import Language, Track  # noqa: E402
from codeverse3d.contracts.spec import Spec  # noqa: E402
from codeverse3d.proc import read_jsonl_lenient  # noqa: E402

HERE = Path(__file__).resolve().parent
BATTERY = HERE / "prompts" / "h2h_scene_v1.yaml"
OUT = HERE / "out" / "h2h_scene_v1"
REPOS = {"scene_multifile": Path("/home/yipeng/projects/scene_multifile"),
         "scene_multifile_graphics": Path("/home/yipeng/projects/scene_multifile_graphics")}
THEIR_RENDERS = Path("app/artifacts/best_parts/_assembly/object_render")
JUDGE_MODEL = "gemini:gemini-3.1-pro-preview"
RUBRIC = "scene_v1"
MAX_VIEWS = 10
OVERVIEW_ORDER = ("overview_front_right", "overview_back_left", "overview_top", "overview_front", "overview_right", "overview_back", "overview_left")
EXTRA_CONTEXT = ("STILLS ONLY: every view is a single frame at one time and NO motion measurement exists for this "
                 "evaluation — answer nothing_moves=false and grade animation_life only from visible motion cues "
                 "(wakes, particles, blur).  The same rule applies to every scene in this comparison.")


# ----------------------------------------------------------------------------- rows
class TheirSide(BaseModel):
    score: float
    score_uncapped: float | None = None
    defect_penalty: float = 0.0
    their_own_score: float | None = None
    their_final_score: float | None = None
    duration_s: float = 0.0
    cost_usd: float = 0.0
    n_views: int = 0
    views: list[str] = []


class OurSide(BaseModel):
    score: float
    score_uncapped: float | None = None
    defect_penalty: float = 0.0
    harness_score: float | None = None
    rounds: int = 0
    best_round: int = 0
    cost_usd: float = 0.0
    minutes: float = 0.0
    n_views: int = 0
    status: str = ""
    views: list[str] = []


class Row(BaseModel):
    id: str
    source_repo: str
    name: str
    theirs: TheirSide
    ours: OurSide | None = None
    delta: float | None = None


def origin(item: BenchPrompt) -> tuple[str, str]:
    repo = next(t for t in item.tags if t in REPOS)
    return repo, item.tags[-1]


# ----------------------------------------------------------------------------- theirs
def their_frames(out_dir: Path) -> list[Path]:
    """Authored cameras first (sorted), then the overview rig in a fixed order; cap MAX_VIEWS."""
    rdir = out_dir / THEIR_RENDERS
    cams = sorted(rdir.glob("view_cam_*.png"))
    over = {p.stem[len("view_"):]: p for p in rdir.glob("view_overview_*.png")}
    ordered = [over[n] for n in OVERVIEW_ORDER if n in over] + [p for n, p in sorted(over.items()) if n not in OVERVIEW_ORDER]
    return (cams + ordered)[:MAX_VIEWS]


def their_meta(out_dir: Path) -> dict[str, Any]:
    rep = json.loads((out_dir / "run_report.json").read_text())
    cost = rep.get("cost") or {}
    scores: list[tuple[int, float]] = []
    for f in sorted(out_dir.glob("trajectories/09_tool_judge_iter*/score.json")):
        s = json.loads(f.read_text()).get("overall_score")
        if isinstance(s, (int, float)):
            scores.append((int(f.parent.name.rsplit("iter", 1)[1]), float(s)))
    scores.sort()
    return {"duration_s": float(rep.get("duration_s") or 0.0),
            "cost_usd": float(cost.get("total_usd_all_iters") or cost.get("total_usd_last_iter") or 0.0),
            "their_own_score": max((s for _, s in scores), default=None),   # best_snapshot ships the best round
            "their_final_score": scores[-1][1] if scores else None}


# ----------------------------------------------------------------------------- ours
def our_frames(run_dir: Path) -> tuple[list[RenderView], dict[str, Any]] | None:
    rec_path = run_dir / "record.json"
    if not rec_path.is_file():
        return None
    rec = json.loads(rec_path.read_text())
    best = int(rec.get("best_round") or 0)
    rnd_path = run_dir / "rounds" / f"r{best:02d}.json"
    if not rnd_path.is_file():
        return None
    rnd = json.loads(rnd_path.read_text())
    views = [RenderView.model_validate(v) for v in (rnd.get("renders") or {}).get("views", [])]
    stills = [v for v in views if (v.time_s or 0.0) == 0.0 and v.mode == "shaded" and Path(v.path).is_file()]
    flagged = [v for v in stills if v.judge] or stills
    chosen = flagged[:MAX_VIEWS]
    minutes = None
    for r in read_jsonl_lenient(run_dir.parent.parent / "results.jsonl", dicts_only=True):
        if r.get("id") == run_dir.name:
            minutes = float(r.get("minutes") or 0.0)
    if minutes is None and rec.get("started_at") and rec.get("finished_at"):
        t0, t1 = (datetime.fromisoformat(rec[k]) for k in ("started_at", "finished_at"))
        minutes = (t1 - t0).total_seconds() / 60
    meta = {"harness_score": rec.get("final_score"), "rounds": len(rec.get("rounds") or []), "best_round": best,
            "cost_usd": float((rec.get("total_usage") or {}).get("cost_usd") or 0.0), "minutes": round(minutes or 0.0, 2),
            "status": str(rec.get("status") or "")}
    return [RenderView(name=v.name, path=v.path, mode="shaded", width=v.width, height=v.height) for v in chosen], meta


# ----------------------------------------------------------------------------- judge
def render_set(views: list[RenderView]) -> RenderSet:
    return RenderSet(views=views, renderer="h2h_stills")


def views_from_paths(paths: list[Path]) -> list[RenderView]:
    return [RenderView(name=p.stem[len("view_"):] if p.stem.startswith("view_") else p.stem, path=str(p), mode="shaded") for p in paths]


def spec_for(item: BenchPrompt) -> Spec:
    return Spec(id=f"h2h_scene_v1/{item.id}", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt=item.prompt, tags=list(item.tags))


def judge_side(judge: Any, spec: Spec, views: list[RenderView], cache: Path) -> dict[str, Any]:
    """One fixed-judge verdict, cached as JSON (re-runs never re-pay)."""
    from codeverse3d.judges.base import JudgeInput

    if cache.is_file():
        return json.loads(cache.read_text())
    inp = JudgeInput(spec=spec, renders=render_set(views), gates=[], acceptance=[], round_index=0, extra_context=EXTRA_CONTEXT)
    j = judge.judge(inp)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(j.model_dump_json(indent=1))
    return json.loads(cache.read_text())


def uncapped(verdict: dict[str, Any]) -> dict[str, Any]:
    """The weighted mean BEFORE defect penalties/caps (scene_v1 penalties can floor a bad scene at 0)."""
    raw = json.loads(verdict.get("raw") or "{}")
    return {"score_uncapped": raw.get("overall_uncapped"), "defect_penalty": float(raw.get("defect_penalty") or 0.0)}


# ----------------------------------------------------------------------------- sheet
def sheet(row_id: str, theirs: list[Path], ours: list[Path], out_png: Path, thumb_h: int = 180) -> None:
    def strip(paths: list[Path], label: str) -> Image.Image:
        thumbs = []
        for p in paths:
            with Image.open(p) as im:
                im = im.convert("RGB")
                thumbs.append(im.resize((max(1, int(im.width * thumb_h / im.height)), thumb_h)))
        w = sum(t.width for t in thumbs) + 4 * max(1, len(thumbs) - 1) if thumbs else 400
        canvas = Image.new("RGB", (w, thumb_h + 22), (20, 20, 20))
        ImageDraw.Draw(canvas).text((4, 4), label, fill=(255, 255, 255))
        x = 0
        for t, p in zip(thumbs, paths, strict=True):
            canvas.paste(t, (x, 22))
            ImageDraw.Draw(canvas).text((x + 3, 24), p.stem[:34], fill=(255, 255, 0))
            x += t.width + 4
        return canvas

    a, b = strip(theirs, f"{row_id} — THEIRS (authored cams + overview)"), strip(ours, f"{row_id} — OURS (best round, t=0 judge views)")
    out = Image.new("RGB", (max(a.width, b.width), a.height + b.height + 6), (60, 60, 60))
    out.paste(a, (0, 0))
    out.paste(b, (0, a.height + 6))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png)


# ----------------------------------------------------------------------------- summary
def sign_test(deltas: list[float]) -> tuple[int, int, float]:
    """(n_nonzero, n_positive, two-sided exact binomial p) for ours − theirs."""
    nz = [d for d in deltas if d != 0]
    n, k = len(nz), sum(1 for d in nz if d > 0)
    if n == 0:
        return 0, 0, 1.0
    cdf = lambda m: sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n  # noqa: E731
    p = min(1.0, 2 * min(cdf(k), 1 - cdf(k - 1)))
    return n, k, p


def summary_md(rows: list[Row]) -> str:
    done = [r for r in rows if r.ours is not None and r.delta is not None]
    lines = ["# h2h_scene_v1 — scene track vs scene_multifile(_graphics)", "",
             f"Fixed judge: `{JUDGE_MODEL}` rubric `{RUBRIC}` n=2, stills only, no gates, no acceptance items; "
             f"≤ {MAX_VIEWS} views per side (theirs: view_cam_* then view_overview_*; ours: best-round t=0 judge views).", "",
             "| id | repo | theirs (fixed) | uncapped | their own | ours (fixed) | uncapped | harness | Δ ours−theirs | their min / $ | our min / $ | views t/o |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t, o = r.theirs, r.ours
        lines.append(f"| {r.id} | {r.source_repo} | {t.score:.3f} | {t.score_uncapped} | {t.their_own_score if t.their_own_score is not None else '—'} | "
                     f"{f'{o.score:.3f}' if o else 'pending'} | {o.score_uncapped if o else '—'} | {o.harness_score if o else '—'} | {f'{r.delta:+.3f}' if r.delta is not None else '—'} | "
                     f"{t.duration_s / 60:.1f} / {t.cost_usd:.2f} | {f'{o.minutes:.1f} / {o.cost_usd:.2f}' if o else '—'} | {t.n_views}/{o.n_views if o else 0} |")
    if done:
        d = [r.delta for r in done if r.delta is not None]
        n, k, p = sign_test(d)
        sd = statistics.stdev(d) if len(d) > 1 else 0.0
        lines += ["", f"**Paired (n={len(d)})**: mean Δ = {statistics.mean(d):+.3f}, sd = {sd:.3f}, "
                  f"sign test: ours better on {k}/{n} (two-sided p = {p:.3f}).",
                  f"Mean minutes/scene: theirs {statistics.mean(r.theirs.duration_s for r in done) / 60:.1f}, ours {statistics.mean(r.ours.minutes for r in done if r.ours):.1f}; "
                  f"mean $/scene: theirs {statistics.mean(r.theirs.cost_usd for r in done):.2f}, ours {statistics.mean(r.ours.cost_usd for r in done if r.ours):.2f}."]
    else:
        lines += ["", "No completed OUR runs yet — theirs judged only."]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ids", default="", help="comma list of prompt ids (default all)")
    ap.add_argument("--theirs-only", action="store_true", help="judge only their side (proof mode)")
    ap.add_argument("--n-samples", type=int, default=2)
    ap.add_argument("--out", type=Path, default=OUT)
    ns = ap.parse_args()
    from codeverse3d.judges.vlm_judge import VlmJudge

    judge = VlmJudge(rubric=RUBRIC, model_id=JUDGE_MODEL, n_samples=ns.n_samples, label="h2h")
    battery = Battery.load(BATTERY)
    want = {s for s in ns.ids.split(",") if s}
    rows: list[Row] = []
    for item in battery.prompts:
        if want and item.id not in want:
            continue
        repo, name = origin(item)
        out_dir = REPOS[repo] / "outputs" / name
        spec = spec_for(item)
        tf = their_frames(out_dir)
        tj = judge_side(judge, spec, views_from_paths(tf), ns.out / "judge" / f"{item.id}_theirs.json")
        theirs = TheirSide(score=tj["overall"], n_views=len(tf), views=[p.name for p in tf], **uncapped(tj), **their_meta(out_dir))
        ours: OurSide | None = None
        of: list[Path] = []
        got = None if ns.theirs_only else our_frames(ns.out / "runs" / item.id)
        if got is not None:
            views, meta = got
            oj = judge_side(judge, spec, views, ns.out / "judge" / f"{item.id}_ours.json")
            ours = OurSide(score=oj["overall"], n_views=len(views), views=[v.name for v in views], **uncapped(oj), **meta)
            of = [Path(v.path) for v in views]
        row = Row(id=item.id, source_repo=repo, name=name, theirs=theirs, ours=ours,
                  delta=round(ours.score - theirs.score, 4) if ours else None)
        rows.append(row)
        sheet(item.id, tf, of, ns.out / "sheets" / f"{item.id}.png")
        print(f"{item.id}: theirs {theirs.score:.3f} (own {theirs.their_own_score}) | ours {ours.score if ours else 'pending'} | Δ {row.delta}")
    ns.out.mkdir(parents=True, exist_ok=True)
    (ns.out / "h2h.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rows))
    (ns.out / "h2h_summary.md").write_text(summary_md(rows))
    print(summary_md(rows))


if __name__ == "__main__":
    main()
