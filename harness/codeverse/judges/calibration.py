"""Judge calibration smoke: re-judge finished runs and ask "does the judge separate good from bad?"

``calibrate(run_dirs)`` reads each run's ``rounds/rNN.json`` (read-only), rebuilds
the ``JudgeInput`` (spec, renders, measurement, gates, plan digest, acceptance),
re-judges every round with ``n_samples`` (montage order shuffled per sample) and
tabulates, per round: gate error count, the stored overall, the new mean/std,
per-criterion std, defects and caps.  Across rounds it reports the Pearson and
Spearman correlation between gate error counts and the new scores (expected
negative), and between stored and new overalls.  Optionally renders a clay /
normals geometry set for the run's best round (``artifacts/object.glb``) so the
geometry montage is exercised.  Output goes to ``out_dir`` (never into the run).

CLI: ``python -m codeverse.judges.calibration runs/a runs/b --model gemini:gemini-3.7-flash --n 3 --out scratch/``
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import RenderSet
from codeverse.contracts.common import TRACK_INFO
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.run import RoundRecord
from codeverse.contracts.spec import Spec
from codeverse.judges.base import JudgeInput, judged_subset, plan_digest, resolve_paths
from codeverse.judges.rubrics import is_degraded
from codeverse.judges.vlm_judge import VlmJudge
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

GEOMETRY_VIEW_NAMES = ("front_right_34", "back_left_34", "top", "low_front_left")


# --------------------------------------------------------------------------- data
class RoundCase(BaseModel):
    """One judgeable round reconstructed from a run directory."""

    run: str
    round_index: int
    kind: str
    rubric: str
    inp: JudgeInput
    gate_errors: int
    gate_warnings: int
    stored: Judgment | None = None
    is_best: bool = False
    glb: str | None = None


class CalibrationRow(BaseModel):
    run: str
    round: int
    kind: str
    gate_errors: int
    gate_warnings: int
    stored_overall: float | None = None
    stored_backend: str = ""
    mean: float
    std: float
    uncapped: float
    defect_penalty: float
    per_criterion_mean: dict[str, float] = Field(default_factory=dict)
    per_criterion_std: dict[str, float] = Field(default_factory=dict)
    defects_present: list[str] = Field(default_factory=list)
    caps: list[str] = Field(default_factory=list)
    passed: bool
    n_used: int
    geometry_views: bool = False
    cost_usd: float = 0.0
    duration_s: float = 0.0
    summary: str = ""
    error: str = ""


class CalibrationTable(BaseModel):
    model_id: str
    n_samples: int
    rows: list[CalibrationRow]
    pearson_errors_vs_score: float | None = None
    spearman_errors_vs_score: float | None = None
    pearson_stored_vs_new: float | None = None
    mean_std: float = 0.0
    mean_criterion_std: float = 0.0
    total_cost_usd: float = 0.0

    def to_markdown(self) -> str:
        head = ("| run | round | kind | gate err/warn | stored | new mean ± std | uncapped | penalty | defects | caps | pass | geo | cost |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        lines = [head]
        for r in self.rows:
            stored = f"{r.stored_overall:.3f}" if r.stored_overall is not None else "-"
            lines.append(
                f"| {r.run} | r{r.round:02d} | {r.kind} | {r.gate_errors}/{r.gate_warnings} | {stored} | "
                f"{r.mean:.3f} ± {r.std:.3f} | {r.uncapped:.3f} | -{r.defect_penalty:.2f} | "
                f"{', '.join(r.defects_present) or '-'} | {', '.join(r.caps) or '-'} | {'Y' if r.passed else 'n'} | "
                f"{'Y' if r.geometry_views else '-'} | ${r.cost_usd:.3f} |" + (f" ERROR {r.error}" if r.error else "")
            )
        crit = sorted({k for r in self.rows for k in r.per_criterion_mean})
        if crit:
            lines.append("")
            lines.append("| run/round | " + " | ".join(crit) + " |")
            lines.append("|---|" + "---|" * len(crit))
            for r in self.rows:
                cells = [f"{r.per_criterion_mean.get(c, float('nan')):.2f}±{r.per_criterion_std.get(c, 0.0):.2f}" if c in r.per_criterion_mean else "-" for c in crit]
                lines.append(f"| {r.run}/r{r.round:02d} | " + " | ".join(cells) + " |")
        fmt = lambda v: "n/a" if v is None else f"{v:+.3f}"  # noqa: E731
        lines.append("")
        lines.append(
            f"model {self.model_id}, n_samples {self.n_samples}: pearson(gate errors, new score) = {fmt(self.pearson_errors_vs_score)}; "
            f"spearman = {fmt(self.spearman_errors_vs_score)}; pearson(stored, new) = {fmt(self.pearson_stored_vs_new)}; "
            f"mean overall std {self.mean_std:.3f}; mean per-criterion std {self.mean_criterion_std:.3f}; cost ${self.total_cost_usd:.3f}"
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- loading runs
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_run_cases(run_dir: Path, *, rounds: list[int] | None = None) -> list[RoundCase]:
    """Rebuild judge inputs for every judgeable round of a run (rounds with renders)."""
    run_dir = Path(run_dir)
    spec = Spec.model_validate_json((run_dir / "spec.json").read_text())
    plan = _read_json(run_dir / "plan.json") if (run_dir / "plan.json").is_file() else {}
    record = _read_json(run_dir / "record.json") if (run_dir / "record.json").is_file() else {}
    best = record.get("best_round")
    acceptance = plan.get("acceptance") or []
    digest = plan_digest(plan)
    ws = Workspace(run_dir)
    glb = run_dir / "artifacts" / "object.glb"
    cases: list[RoundCase] = []
    for path in sorted((run_dir / "rounds").glob("r*.json")):
        rec = RoundRecord.model_validate_json(path.read_text())
        if rounds is not None and rec.index not in rounds:
            continue
        if rec.renders is None or not rec.renders.views:
            continue
        rubric = rec.judgment.rubric if rec.judgment else TRACK_INFO[spec.track].rubric
        # the same views, at the same paths, that the in-run judge and `3dcv judge` see
        inp = JudgeInput(
            spec=spec, renders=judged_subset(resolve_paths(ws, rec.renders)), measurement=rec.measurement, gates=rec.gates,
            acceptance=[AcceptanceItem.model_validate(a) for a in acceptance], plan_summary=digest,
            round_index=rec.index,
        )
        cases.append(RoundCase(
            run=run_dir.name, round_index=rec.index, kind=rec.kind, rubric=rubric, inp=inp,
            gate_errors=sum(len(g.errors) for g in rec.gates),
            gate_warnings=sum(1 for g in rec.gates for f in g.findings if f.severity.value == "warn"),
            stored=rec.judgment, is_best=(best == rec.index) if best is not None else False,
            glb=str(glb) if glb.is_file() else None,
        ))
    if best is None and cases:  # no record: treat the last round as the one the GLB belongs to
        cases[-1].is_best = True
    return cases


def render_geometry_views(case: RoundCase, out_dir: Path, mode: str) -> RenderSet | None:
    """Clay/normals renders of the run's final GLB for the best round (object tracks only)."""
    if not case.glb or case.inp.spec.track.value == "scene":
        return None
    from codeverse.conventions import OBJECT_VIEWS
    from codeverse.spatial.render import render_glb

    views = [v for v in OBJECT_VIEWS if v.name in GEOMETRY_VIEW_NAMES]
    try:
        return render_glb(case.glb, out_dir / case.run / f"geometry_{mode}", views=views, mode=mode, sheet=False)
    except Exception as e:  # noqa: BLE001 — calibration must not die on a render hiccup
        log.warning("geometry render failed for %s: %s", case.run, e)
        return None


# --------------------------------------------------------------------------- statistics
def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return round(num / den, 4) if den else None


def _ranks(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    return pearson(_ranks(xs), _ranks(ys))


# --------------------------------------------------------------------------- main entry
def _judge_case(case: RoundCase, judge: VlmJudge, geometry: RenderSet | None, out: Path) -> CalibrationRow:
    t0 = time.time()
    j = judge.judge(case.inp, geometry_views=geometry)
    raw = json.loads(j.raw) if j.raw else {}
    jdir = out / "judgments"
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / f"{case.run}_r{case.round_index:02d}_{judge.model_id.replace(':', '_')}.json").write_text(j.model_dump_json(indent=1))
    return CalibrationRow(
        run=case.run, round=case.round_index, kind=case.kind, gate_errors=case.gate_errors, gate_warnings=case.gate_warnings,
        stored_overall=case.stored.overall if case.stored else None,
        stored_backend=case.stored.judge_backend if case.stored else "",
        mean=j.overall, std=j.score_std, uncapped=float(raw.get("overall_uncapped", j.overall)),
        defect_penalty=float(raw.get("defect_penalty", 0.0)),
        per_criterion_mean=dict(j.scores), per_criterion_std=dict(raw.get("per_criterion_std", {})),
        defects_present=[d for d, on in raw.get("defects", {}).items() if on],
        caps=[c["rule"] for c in raw.get("caps", {}).get("caps_applied", [])],
        passed=j.passed, n_used=j.n_samples, geometry_views=geometry is not None,
        cost_usd=j.usage.cost_usd, duration_s=round(time.time() - t0, 1), summary=j.summary,
        error=j.summary if is_degraded(j) else "",
    )


def calibrate(
    run_dirs: list[Path | str],
    *,
    model_id: str | None = None,
    n_samples: int = 3,
    out_dir: Path | str | None = None,
    geometry_mode: str | None = "clay",
    rounds: list[int] | None = None,
    thinking: str = "low",
    max_workers: int = 4,
    chat_model: Any = None,
) -> CalibrationTable:
    """Re-judge every round of ``run_dirs`` with ``n_samples`` and tabulate separation/noise.

    Writes ``calibration_<model>.json`` / ``.md`` into ``out_dir`` (default: cwd) and
    never touches the run directories.  ``chat_model`` injects a ChatModel (tests).
    """
    from codeverse.config import get_settings

    model_id = model_id or get_settings().default_judge
    out = Path(out_dir) if out_dir else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    cases: list[RoundCase] = []
    for rd in run_dirs:
        cases.extend(load_run_cases(Path(rd), rounds=rounds))
    if not cases:
        raise ValueError(f"no judgeable rounds found under {list(map(str, run_dirs))}")
    geometry: dict[int, RenderSet | None] = {}
    for i, c in enumerate(cases):
        geometry[i] = render_geometry_views(c, out, geometry_mode) if (geometry_mode and c.is_best) else None
    judges = {c.rubric: VlmJudge(c.rubric, model_id=model_id, n_samples=n_samples, thinking=thinking,  # type: ignore[arg-type]
                                 cache_dir=out / "cache", label="calibrate", chat_model=chat_model) for c in cases}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        rows = list(pool.map(lambda ic: _judge_case(ic[1], judges[ic[1].rubric], geometry[ic[0]], out), enumerate(cases)))
    ok = [r for r in rows if not r.error]
    table = CalibrationTable(
        model_id=model_id, n_samples=n_samples, rows=rows,
        pearson_errors_vs_score=pearson([float(r.gate_errors) for r in ok], [r.mean for r in ok]),
        spearman_errors_vs_score=spearman([float(r.gate_errors) for r in ok], [r.mean for r in ok]),
        pearson_stored_vs_new=pearson([r.stored_overall for r in ok if r.stored_overall is not None],
                                      [r.mean for r in ok if r.stored_overall is not None]),
        mean_std=round(statistics.fmean([r.std for r in ok]), 4) if ok else 0.0,
        mean_criterion_std=round(statistics.fmean([v for r in ok for v in r.per_criterion_std.values()] or [0.0]), 4),
        total_cost_usd=round(sum(r.cost_usd for r in rows), 4),
    )
    stem = "calibration_" + model_id.replace(":", "_").replace("/", "_")
    (out / f"{stem}.json").write_text(json.dumps(table.model_dump(mode="json"), indent=1, ensure_ascii=False))
    (out / f"{stem}.md").write_text(table.to_markdown() + "\n")
    return table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-judge finished runs to calibrate the VLM judge.")
    ap.add_argument("run_dirs", nargs="+")
    ap.add_argument("--model", default=None, help="chat model id, e.g. gemini:gemini-3.7-flash")
    ap.add_argument("--n", type=int, default=3, help="samples per round")
    ap.add_argument("--out", default=".", help="output directory (json + md)")
    ap.add_argument("--geometry", default="clay", help="clay | normals | none (geometry montage for the best round)")
    ap.add_argument("--rounds", default="", help="comma-separated round indices (default all)")
    ap.add_argument("--thinking", default="low")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rounds = [int(x) for x in a.rounds.split(",") if x.strip()] or None
    table = calibrate(a.run_dirs, model_id=a.model, n_samples=a.n, out_dir=a.out,
                      geometry_mode=None if a.geometry == "none" else a.geometry, rounds=rounds,
                      thinking=a.thinking, max_workers=a.workers)
    print(table.to_markdown())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
