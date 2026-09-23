"""Calibrate the GRAPHICS judge against a human reading of the same contact sheets.

The loop the object track had (rubric → re-judge recorded rounds → compare against what a
person sees → revise) never ran for graphics.  This script is that loop's measuring half:

    python bench/judge_calib_graphics.py --corpus <corpus.json> --eye <eye.json> \
        --rubrics shader_v1,shader_v2 --model gemini:gemini-3.1-pro-preview --n 2 \
        --out bench/out/judge_calib_graphics

* ``corpus.json``: ``[[score, run_dir, sheet_path, generator], ...]`` — every graphics run with a
  judged round (``run_dir`` relative to ``bench/out`` or absolute).
* ``eye.json``: ``{run_dir: score}`` — a person's 0–1 reading of the SAME sheets ("would a
  curator screenshot it / does it look like the thing").  The ground truth this calibrates to.

For every run and every rubric it rebuilds the JudgeInput the loop used (spec, the picked round's
RenderSet and gates; no planner acceptance items, so the rubric is measured on its own) and
judges it with a fresh ``VlmJudge`` (n samples, pro).  Output per rubric: ``<rubric>.jsonl`` (one
row per run: overall, per-criterion, defects present, caps) and one ``summary.md`` with
Spearman(judge, eye), mean judge vs mean eye, the biggest disagreements, and defect firing rates.
Judging only; nothing under the runs is modified.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from codeverse3d.addons import select
from codeverse3d.addons.calibration import spearman
from codeverse3d.contracts.artifacts import GateReport, RenderSet
from codeverse3d.contracts.plan import GraphicsPlan
from codeverse3d.contracts.spec import Spec
from codeverse3d.judges.base import JudgeInput, plan_summary
from codeverse3d.judges.vlm_judge import VlmJudge
from codeverse3d.record.record import RecordError
from codeverse3d.tracks.graphics import frame_stats_text
from codeverse3d.workspace import Workspace

OUT_ROOT = Path(__file__).resolve().parent / "out"


def picked_round(run: Path) -> dict[str, Any] | None:
    """The round ``codeverse3d.addons.select`` picks, as its rounds/rNN.json (None: nothing judged)."""
    try:
        idx = select.pick(run)
    except RecordError:
        return None
    path = run / "rounds" / f"r{idx:02d}.json" if idx is not None else None
    return json.loads(path.read_text()) if path is not None and path.is_file() else None


def judge_one(run: Path, rubric: str, model: str, n: int) -> dict[str, Any]:
    spec = Spec.model_validate(json.loads((run / "spec.json").read_text()))
    rnd = picked_round(run)
    if rnd is None:
        return {"run": str(run), "error": "no judged round"}
    renders = RenderSet.model_validate(rnd["renders"])
    gates = [GateReport.model_validate(g) for g in rnd.get("gates") or []]
    # what the in-run judge also saw: the plan digest and the round's measured frame metrics
    # (frame_stats_text: its own copy, never a later build's — without them, pro called four
    # moving effects "static" in the first pass)
    plan_path = run / "plan.json"
    digest = plan_summary(GraphicsPlan.model_validate_json(plan_path.read_text()), spec.language) if plan_path.is_file() else ""
    extra = "FRAME METRICS (harness-measured):\n" + frame_stats_text(Workspace(run), int(rnd.get("index", 0)))
    inp = JudgeInput(spec=spec, renders=renders, gates=gates, round_index=int(rnd.get("index", 0)),
                     plan_summary=digest, extra_context=extra)
    judge = VlmJudge(rubric=rubric, model_id=model, n_samples=n)
    v = judge.judge(inp)
    raw = v.raw if isinstance(v.raw, dict) else json.loads(v.raw or "{}")
    return {
        "run": str(run), "rubric": rubric, "overall": v.overall, "loop_overall": (rnd.get("judgment") or {}).get("overall"),
        "scores": v.scores, "uncapped": raw.get("overall_uncapped"),
        "defects": [d for d, on in (raw.get("defects") or {}).items() if on],
        "caps": [c.get("rule") for c in (raw.get("caps") or {}).get("caps_applied", [])],
        "summary": (v.summary or "")[:400], "cost_usd": getattr(v.usage, "cost_usd", None),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--eye", required=True)
    ap.add_argument("--rubrics", default="shader_v1,shader_v2")
    ap.add_argument("--model", default="gemini:gemini-3.1-pro-preview")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(OUT_ROOT / "judge_calib_graphics"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    corpus = json.loads(Path(a.corpus).read_text())
    eye: dict[str, float] = json.loads(Path(a.eye).read_text())

    def norm(d: str) -> str:
        p = Path(d)
        return str(p.relative_to(OUT_ROOT)) if p.is_absolute() and OUT_ROOT in p.parents else d.replace("bench/out/", "")

    runs = [(norm(row[1]), Path(row[1]) if Path(row[1]).is_absolute() else Path.cwd() / row[1]) for row in corpus]
    runs = [(k, p) for k, p in runs if k in eye]
    lines = [f"# graphics judge calibration — {len(runs)} runs, model {a.model}, n={a.n}\n"]
    for rubric in [r.strip() for r in a.rubrics.split(",") if r.strip()]:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            rows = list(ex.map(lambda kp, rb=rubric: {"key": kp[0], **judge_one(kp[1], rb, a.model, a.n)}, runs))
        (out / f"{rubric}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        ok = [r for r in rows if "overall" in r]
        js = [r["overall"] for r in ok]
        ey = [eye[r["key"]] for r in ok]
        rho = spearman(js, ey)
        rho_s = "n/a" if rho is None else f"{rho:.2f}"
        loop = [r["loop_overall"] for r in ok if r.get("loop_overall") is not None]
        lines.append(f"\n## {rubric}\n")
        lines.append(f"Spearman(judge, eye) = **{rho_s}** · mean judge {st.mean(js):.3f} · mean eye {st.mean(ey):.3f}"
                     + (f" · loop-time mean {st.mean(loop):.3f}" if loop else "") + f" · n={len(ok)}\n")
        fired: dict[str, int] = {}
        for r in ok:
            for d in r["defects"]:
                fired[d] = fired.get(d, 0) + 1
        lines.append("defects fired: " + ", ".join(f"{d} {c}/{len(ok)}" for d, c in sorted(fired.items(), key=lambda x: -x[1])) + "\n")
        lines.append("\n| run | eye | judge | Δ(judge−eye) | defects | caps |\n|---|---|---|---|---|---|\n")
        for r in sorted(ok, key=lambda r: -(abs(r["overall"] - eye[r["key"]]))):
            lines.append(f"| {r['key'].split('/')[-1]} | {eye[r['key']]:.2f} | {r['overall']:.3f} | {r['overall'] - eye[r['key']]:+.2f} | "
                         f"{', '.join(r['defects'])} | {', '.join(c for c in r['caps'] if c)} |\n")
        for r in rows:
            if "error" in r:
                lines.append(f"- {r['key']}: {r['error']}\n")
    (out / "summary.md").write_text("".join(lines))
    print("".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
