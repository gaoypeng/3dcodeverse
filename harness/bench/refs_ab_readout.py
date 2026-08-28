"""Read out a reference-photo A/B: judge BOTH arms offline with the same LikenessJudge.

    python bench/refs_ab_readout.py bench/out/refs_v1_graphics --refs bench/refs/tsr_gfx_aurora_ridge \
        --rubric shader_v2 --model gemini:gemini-3.1-pro-preview --n 2

The in-loop judge differs by arm on purpose (the arm with photos is judged by LikenessJudge,
the arm without by the plain rubric judge), so loop-time scores cannot be paired.  This script
takes each run's best round (sheet, frames, gates, plan digest, frame metrics — what the loop
hands the judge), attaches the SAME reference photos to every run's spec, and judges all of
them with one LikenessJudge.  Arms are read from the prompt id (``<name>_ref_<seed>`` /
``<name>_noref_<seed>``); the readout pairs seeds and prints per-run scores, defects, and the
paired mean delta.  Verdicts are cached under ``<out>/refs_ab_judge/<id>.json``.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import GateReport, RenderSet
from codeverse.contracts.spec import ReferenceImage, Spec
from codeverse.judges.base import JudgeInput
from codeverse.judges.reference import LikenessJudge
from codeverse.judges.replay_input import plan_digest
from codeverse.tracks.graphics import frame_stats_text
from codeverse.workspace import Workspace

SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def best_round(run: Path) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for f in sorted(run.glob("rounds/r*.json")):
        r = json.loads(f.read_text())
        j = r.get("judgment") or {}
        if j.get("overall") is not None and (best is None or j["overall"] > (best.get("judgment") or {}).get("overall", -1)):
            best = r
    return best


def judge_run(run: Path, refs: list[Path], judge: LikenessJudge, cache: Path) -> dict[str, Any]:
    cached = cache / f"{run.name}.json"
    if cached.is_file():
        return json.loads(cached.read_text())
    rnd = best_round(run)
    if rnd is None:
        return {"id": run.name, "error": "no judged round"}
    spec = Spec.model_validate(json.loads((run / "spec.json").read_text()))
    spec = spec.model_copy(update={"references": [ReferenceImage(path=str(p), role="likeness", note=p.stem.replace("_", " ")) for p in refs]})
    ws = Workspace(run)
    plan_path = run / "plan.json"
    last_index = max((int(f.stem[1:]) for f in run.glob("rounds/r*.json")), default=0)
    extra = ("FRAME METRICS (harness-measured):\n" + frame_stats_text(ws)) if int(rnd.get("index", 0)) == last_index else \
        "FRAME METRICS: not available for this round; judge motion from the frames."
    inp = JudgeInput(spec=spec, renders=RenderSet.model_validate(rnd["renders"]),
                     gates=[GateReport.model_validate(g) for g in rnd.get("gates") or []],
                     round_index=int(rnd.get("index", 0)),
                     plan_summary=plan_digest(json.loads(plan_path.read_text())) if plan_path.is_file() else "", extra_context=extra)
    v = judge.judge(inp)
    raw = v.raw if isinstance(v.raw, dict) else json.loads(v.raw or "{}")
    row = {"id": run.name, "round": int(rnd.get("index", 0)), "overall": v.overall, "loop_overall": (rnd.get("judgment") or {}).get("overall"),
           "uncapped": raw.get("overall_uncapped"), "scores": v.scores,
           "defects": [d for d, on in (raw.get("defects") or {}).items() if on],
           "caps": [c.get("rule") for c in (raw.get("caps") or {}).get("caps_applied", [])], "summary": (v.summary or "")[:500]}
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(row, indent=1))
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", help="bench/out/<battery> of the A/B")
    ap.add_argument("--refs", required=True, help="folder of the reference photos to attach to EVERY run")
    ap.add_argument("--rubric", default="shader_v2")
    ap.add_argument("--model", default="gemini:gemini-3.1-pro-preview")
    ap.add_argument("--n", type=int, default=2)
    a = ap.parse_args(argv)
    out = Path(a.out)
    refs = sorted(p for p in Path(a.refs).iterdir() if p.suffix.lower() in SUFFIXES)
    judge = LikenessJudge(a.model, n_samples=a.n, rubric=a.rubric)
    rows = [judge_run(run, refs, judge, out / "refs_ab_judge") for run in sorted((out / "runs").iterdir()) if (run / "spec.json").is_file()]
    ok = {r["id"]: r for r in rows if "overall" in r}
    print(f"# refs A/B readout — {len(ok)} runs judged with LikenessJudge({a.rubric}, {a.model}, n={a.n}), photos: {[p.name for p in refs]}\n")
    print("| run | round | loop score | same-judge score | defects | caps |\n|---|---|---|---|---|---|")
    for rid, r in sorted(ok.items()):
        print(f"| {rid} | r{r['round']:02d} | {r['loop_overall']:.3f} | **{r['overall']:.3f}** | {', '.join(r['defects'])} | {', '.join(c for c in r['caps'] if c)} |")
    seeds: dict[str, dict[str, float]] = {}
    for rid, r in ok.items():
        name, arm, seed = rid.rsplit("_", 2)
        seeds.setdefault(seed, {})[arm] = r["overall"]
    deltas = [s["ref"] - s["noref"] for s in seeds.values() if "ref" in s and "noref" in s]
    if deltas:
        m = st.mean(deltas)
        sd = st.stdev(deltas) if len(deltas) > 1 else float("nan")
        print(f"\npaired (ref − noref): n={len(deltas)} mean {m:+.3f} sd {sd:.3f} sign {sum(d > 0 for d in deltas)}/{len(deltas)}"
              f"  (judged-score A/A floor sd 0.202)")
    for r in rows:
        if "error" in r:
            print(f"- {r['id']}: {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
