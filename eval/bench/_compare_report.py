"""Result rows + report.md / report.html for ``bench/compare_backends.py``.

``CellResult`` is one (prompt, arm) cell; ``PairRow`` one pairwise verdict.
``build_compare_report(out_dir)`` reads ``results.jsonl`` + ``pairwise.jsonl``
and writes the arm summary table, the per-prompt score matrix, the pairwise
arena (win rates) and an HTML gallery of the contact sheets side by side.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import statistics
from pathlib import Path

from pydantic import BaseModel, Field

from bench._fixed_eval import RUBRIC, rubric_for
from bench._jsonl import latest, read_jsonl
from codeverse3d.contracts.common import Track


def battery_rubric(meta: dict) -> str:
    """The fixed judge's rubric the report header names: ``rubric_for`` on the battery's track."""
    track = str(meta.get("battery", {}).get("track") or Track.STATIC_OBJECT.value)
    try:
        return rubric_for(Track(track))
    except ValueError:  # a meta.json written before batteries carried a track
        return RUBRIC


class CellResult(BaseModel):
    prompt_id: str
    tier: str = ""
    arm: str
    kind: str
    target: str = ""
    judge: str = ""
    status: str = Field(default="",
        description="scored | build_failed | no_code | judge_error | error | infra_failed "
                    "(provider outage: excluded from every rate, see bench/_infra.py)")
    score: float | None = Field(default=None, description="fixed-judge overall (0 when the build failed)")
    score_std: float = 0.0
    passed: bool | None = None
    build_ok: bool = False
    gate_errors: list[str] = Field(default_factory=list)
    tris: int | None = None
    gen_cost_usd: float = Field(default=0.0, description="generation cost (harness: whole run incl. its loop judge)")
    judge_cost_usd: float = Field(default=0.0, description="fixed-judge cost for this cell")
    gen_input_tokens: int = Field(default=0, description="generation input tokens (uncached + cached), whole run")
    gen_output_tokens: int = Field(default=0, description="generation output tokens incl. thinking, whole run")
    gen_cached_tokens: int = Field(default=0, description="the cache-read share of gen_input_tokens")
    gen_seconds: float = 0.0
    wall_s: float = Field(default=0.0, description="the cell's clock, provider waits included: what flag_degraded reads")
    minutes: float | None = Field(default=None, description=(
        "the cell's minutes (docs/COST.md §31): a harness arm's RunRecord.minutes, a one-shot or bare-agent "
        "arm's generation time net of provider errors; None on a row written before 2026-09-22"))
    attempts: int = 0
    tool_calls: int = 0
    criteria: dict[str, float] = Field(default_factory=dict)
    harness_status: str = ""
    harness_rounds: int = Field(default=0, description="rounds the harness run COMPLETED (baseline included)")
    harness_loop_score: float | None = None
    harness_stop_reason: str = Field(default="", description="record.extra['stop_reason'] of the harness run")
    harness_aborted_rounds: int = Field(default=0, description="rounds the budget / wall-clock ceiling cut mid-way (record.extra['aborted_rounds'])")
    degraded: bool = Field(default=False, description=(
        "harness run stopped on the wall-clock ceiling with <=1 completed round and money left: it waited "
        "on the provider, it did not iterate.  Scored (the artifact is real) but flagged, so a storm cannot "
        "pass as harness performance (compare_backends.flag_degraded, docs/EVAL.md)"))
    degraded_reason: str = ""
    sheet: str = ""
    glb: str = ""
    workspace: str = ""
    error: str = ""
    error_is_infra: bool = Field(default=False, description=(
        "set at the raise site, where the exception still carries .status/.__cause__.  "
        "run_cell's string fallback cannot recover those, so without this the one-shot "
        "arm scored 0.0 for the same outage that dropped the harness arm."))

    def natural_key(self) -> tuple[str, ...]:
        """Row identity: one cell is one (prompt, arm).  results.jsonl is append-only, so
        a resume / ``--redo-status`` re-run appends a SECOND row for the same key."""
        return (self.prompt_id, self.arm)

    def note(self, msg: str) -> None:
        """Append to the error trail.  The row is the record of last resort: the failure,
        classifying it and writing cell.json can each go wrong in one cell, and every one
        of them has to survive into results.jsonl."""
        self.error = f"{self.error}; {msg}" if self.error else msg


def cell_minutes(r: CellResult) -> float:
    """A cell's minutes; a row written before step timing counts its own clock, as a record does."""
    return r.wall_s / 60 if r.minutes is None else r.minutes


class PairRow(BaseModel):
    prompt_id: str
    arm_a: str
    arm_b: str
    winner: str = Field(description="a | b | tie | unavailable")
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    orderings: list[dict] = Field(default_factory=list)
    cost_usd: float = 0.0
    judged: bool = Field(default=True, description="False when decided by a missing build (no judge call)")
    error: str = ""
    eligible: bool = Field(default=True, description="False when an infrastructure-failed cell makes this pair unavailable")
    exclusion_reason: str = ""
    rubric: str = Field(default="", description="Track rubric used by the pairwise judge; empty on historical rows")

    def natural_key(self) -> tuple[str, ...]:
        """Row identity: one pairwise verdict is one (prompt, arm A, arm B)."""
        return (self.prompt_id, self.arm_a, self.arm_b)


# ----------------------------------------------------------------------------- aggregates
class ArmStats(BaseModel):
    arm: str
    kind: str
    n: int = Field(description="cells attempted")
    n_evaluated: int = Field(default=0, description="cells that actually ran (n minus provider outages)")
    n_scored: int = Field(default=0, description="cells that ran and carry a fixed-judge score")
    infra_failed: int = Field(default=0, description="cells dropped: the provider, not the model, failed")
    budget_exhausted: int = Field(default=0, description="cells that ran out of time/money with no artifact")
    mean_score: float | None
    median_score: float | None
    pass_rate: float | None
    build_ok_rate: float
    mean_gen_usd: float
    mean_judge_usd: float
    mean_minutes: float
    tool_calls: int
    errors: int
    degraded: int = Field(default=0, description="harness cells flagged storm-degraded (scored, but the run never iterated)")
    ceiling_cut: int = Field(default=0, description="harness cells whose run had a round cut by the ceiling")


class PairStats(BaseModel):
    arm_a: str
    arm_b: str
    n: int
    wins_a: int
    wins_b: int
    ties: int
    win_rate_a: float | None = Field(description="wins_a / (wins_a + wins_b); ties excluded")
    mean_confidence: float


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 4) if xs else None


def arm_stats(rows: list[CellResult]) -> list[ArmStats]:
    by_arm: dict[str, list[CellResult]] = {}
    for r in rows:
        by_arm.setdefault(r.arm, []).append(r)
    out = []
    for arm, rs in by_arm.items():
        # a provider outage never tested the model: it must not land in ANY rate, or
        # an arm unlucky with the weather looks worse than one that ran in the clear
        ev = [r for r in rs if r.status != "infra_failed"]
        scores = [r.score for r in ev if r.score is not None]
        passed = [r.passed for r in ev if r.passed is not None]
        out.append(ArmStats(
            arm=arm, kind=rs[0].kind, n=len(rs), n_evaluated=len(ev), n_scored=len(scores),
            infra_failed=len(rs) - len(ev),
            budget_exhausted=sum(1 for r in ev if r.status == "budget_exhausted"), mean_score=_mean(scores),
            median_score=round(statistics.median(scores), 4) if scores else None,
            pass_rate=round(sum(passed) / len(passed), 4) if passed else None,
            build_ok_rate=round(sum(r.build_ok for r in ev) / len(ev), 4) if ev else 0.0,
            mean_gen_usd=round(statistics.fmean(r.gen_cost_usd for r in ev), 4) if ev else 0.0,
            mean_judge_usd=round(statistics.fmean(r.judge_cost_usd for r in ev), 4) if ev else 0.0,
            mean_minutes=round(statistics.fmean(cell_minutes(r) for r in ev), 2) if ev else 0.0,
            tool_calls=sum(r.tool_calls for r in ev), errors=sum(1 for r in rs if r.status in ("error", "judge_error")),
            degraded=sum(1 for r in ev if r.degraded), ceiling_cut=sum(1 for r in ev if r.harness_aborted_rounds > 0),
        ))
    return sorted(out, key=lambda s: (s.kind != "harness", -(s.mean_score or -1)))


def pair_stats(pairs: list[PairRow]) -> list[PairStats]:
    groups: dict[tuple[str, str], list[PairRow]] = {}
    for p in pairs:
        if not p.eligible:
            continue
        groups.setdefault((p.arm_a, p.arm_b), []).append(p)
    out = []
    for (a, b), ps in groups.items():
        wa, wb = sum(p.winner == "a" for p in ps), sum(p.winner == "b" for p in ps)
        out.append(PairStats(arm_a=a, arm_b=b, n=len(ps), wins_a=wa, wins_b=wb, ties=len(ps) - wa - wb,
                             win_rate_a=round(wa / (wa + wb), 4) if wa + wb else None,
                             mean_confidence=round(statistics.fmean(p.confidence for p in ps), 3)))
    return out


# ----------------------------------------------------------------------------- markdown
def _prompt_order(rows: list[CellResult], meta: dict) -> list[str]:
    """Battery order, restricted to prompts that have at least one cell."""
    seen = {r.prompt_id for r in rows}
    ordered = [p["id"] for p in meta.get("battery", {}).get("prompts", []) if p["id"] in seen]
    return ordered or sorted(seen)


def _arm_order(rows: list[CellResult], meta: dict) -> list[str]:
    """matrix.json arm order first, then any extra arms found in results (a later
    partial re-run — e.g. gemini arms only — must not drop earlier arms' columns)."""
    listed = [a for a in meta.get("arms", []) or []]
    return listed + sorted({r.arm for r in rows} - set(listed))


def _f(v: float | None, spec: str = ".3f") -> str:
    return "-" if v is None else format(v, spec)


def _cell_text(r: CellResult | None) -> str:
    if r is None:
        return "·"
    if r.score is None:
        return f"? ({r.status})"
    flag = "" if r.build_ok else " ✗build"
    return f"{r.score:.2f}{'✓' if r.passed else ''}{flag}{'†' if r.degraded else ''}"


def compare_markdown(out: Path, rows: list[CellResult], pairs: list[PairRow], meta: dict) -> str:
    opts = meta.get("options", {})
    arms = _arm_order(rows, meta)
    prompts = _prompt_order(rows, meta)
    cells = latest(rows)
    md = [f"# harness vs one-shot — {meta.get('battery', {}).get('name', out.name)}", "",
          f"fixed judge: **{opts.get('judge', '?')}** (rubric {battery_rubric(meta)}, n_samples={opts.get('n_samples', 2)}, "
          f"acceptance = must_have list) · harness loop judge: {opts.get('loop_judge') or 'settings default'} · "
          f"harness rounds ≤ {opts.get('rounds', '?')} · cells: {len(rows)}", "",
          "Every arm's final `src/model.py` is re-built, re-rendered and judged by the same evaluator; a failed "
          "build scores 0.  `$gen` for harness arms is the whole run (planner + generator + its loop judge); for "
          "one-shot arms it is the single call (subscription CLIs report 0 unless the CLI returns a cost).  "
          "`n` counts cells that actually ran; `dropped` counts cells lost to a provider outage "
          "(503 storm, timeout, exhausted pool) — those test nothing about the model and are excluded "
          "from every rate on this page rather than scored 0.", "",
          "`degraded` counts harness cells that stopped on the wall-clock ceiling with at most one completed round "
          "and money left — the run waited on the provider instead of iterating; they are scored (the artifact is "
          "real) but marked † so a storm cannot pass as harness performance.  `cut` counts harness runs the "
          "ceiling interrupted mid-round.", "",
          "## arms", "", "| arm | kind | n | dropped | over budget | mean | median | pass | build ok | $gen | $judge | min | tool calls | errors | degraded | cut |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in arm_stats(rows):
        md.append(f"| {s.arm} | {s.kind} | {s.n_evaluated} | {s.infra_failed or ''} | {s.budget_exhausted or ''} | {_f(s.mean_score)} | {_f(s.median_score)} | {_f(s.pass_rate, '.0%')} | "
                  f"{s.build_ok_rate:.0%} | {s.mean_gen_usd:.2f} | {s.mean_judge_usd:.3f} | {s.mean_minutes:.1f} | "
                  f"{s.tool_calls} | {s.errors} | {s.degraded or ''} | {s.ceiling_cut or ''} |")
    md += ["", "## per prompt (score, ✓ = passed, ✗build = build failed, † = storm-degraded harness run)", "",
           "| prompt | tier | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    for pid in prompts:
        tier = next((r.tier for r in rows if r.prompt_id == pid), "")
        md.append(f"| {pid} | {tier} | " + " | ".join(_cell_text(cells.get((pid, a))) for a in arms) + " |")
    md += ["", "## pairwise arena (PairwiseJudge, both orders; ties = orderings disagree or equal)", "",
           "| harness arm (A) | one-shot arm (B) | n | A wins | B wins | ties | A win rate | conf |", "|---|---|---|---|---|---|---|---|"]
    for s in pair_stats(pairs):
        md.append(f"| {s.arm_a} | {s.arm_b} | {s.n} | {s.wins_a} | {s.wins_b} | {s.ties} | {_f(s.win_rate_a, '.0%')} | {s.mean_confidence:.2f} |")
    if pairs:
        md += ["", "### pairwise verdicts", "", "| prompt | A | B | winner | conf | judged | reason |", "|---|---|---|---|---|---|---|"]
        for p in sorted(pairs, key=lambda p: (p.prompt_id, p.arm_a, p.arm_b)):
            md.append(f"| {p.prompt_id} | {p.arm_a} | {p.arm_b} | {p.winner} | {p.confidence:.2f} | {'yes' if p.judged else 'no'} | "
                      f"{html.escape((p.reasons[0] if p.reasons else p.error)[:160])} |")
    hr = [r for r in rows if r.kind == "harness"]
    if hr:
        md += ["", "## harness runs (loop judge score vs fixed judge score)", "",
               "| prompt | arm | run status | stop | rounds | cut | loop score | fixed score | $run | min | degraded |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        md += [f"| {r.prompt_id} | {r.arm} | {r.harness_status} | {r.harness_stop_reason} | {r.harness_rounds} | {r.harness_aborted_rounds or ''} | "
               f"{_f(r.harness_loop_score)} | {_f(r.score)} | {r.gen_cost_usd:.2f} | {cell_minutes(r):.1f} | {'† ' + r.degraded_reason if r.degraded else ''} |"
               for r in sorted(hr, key=lambda r: (r.prompt_id, r.arm))]
    total_gen, total_judge, total_pw = sum(r.gen_cost_usd for r in rows), sum(r.judge_cost_usd for r in rows), sum(p.cost_usd for p in pairs)
    md += ["", f"**Total cost**: generation ${total_gen:.2f} · fixed judge ${total_judge:.2f} · pairwise ${total_pw:.2f} "
           f"(subscription CLIs report their own cost figure or 0)."]
    errs = [r for r in rows if r.error]
    if errs:
        md += ["", "## errors", ""]
        md += [f"- `{r.prompt_id}` / `{r.arm}` ({r.status}): {r.error.splitlines()[0][:300]}" for r in errs]
    return "\n".join(md) + "\n"


# ----------------------------------------------------------------------------- html gallery
_STYLE = ("body{font-family:system-ui,sans-serif;margin:24px;background:#111;color:#eee}table{border-collapse:collapse;margin:12px 0}"
          "td,th{border:1px solid #444;padding:4px 8px;font-size:13px;vertical-align:top}th{background:#222}"
          ".g img{width:100%;display:block;border-radius:4px}.g td{min-width:260px;max-width:360px}.ok{color:#8f8}.bad{color:#f88}"
          "pre{white-space:pre-wrap;font-size:11px;color:#bbb}")


def compare_html(out: Path, rows: list[CellResult], pairs: list[PairRow], meta: dict) -> str:
    assets = out / "report_assets"
    assets.mkdir(exist_ok=True)
    arms = _arm_order(rows, meta)
    prompts = _prompt_order(rows, meta)
    cells = latest(rows)
    head = "<tr><th>arm</th><th>n</th><th>mean</th><th>pass</th><th>build ok</th><th>$gen</th><th>min</th></tr>"
    summ = "".join(f"<tr><td>{html.escape(s.arm)}</td><td>{s.n}</td><td>{_f(s.mean_score)}</td><td>{_f(s.pass_rate, '.0%')}</td>"
                   f"<td>{s.build_ok_rate:.0%}</td><td>{s.mean_gen_usd:.2f}</td><td>{s.mean_minutes:.1f}</td></tr>" for s in arm_stats(rows))
    pw = "".join(f"<tr><td>{html.escape(s.arm_a)}</td><td>{html.escape(s.arm_b)}</td><td>{s.wins_a}/{s.wins_b}/{s.ties}</td>"
                 f"<td>{_f(s.win_rate_a, '.0%')}</td></tr>" for s in pair_stats(pairs))
    grid = ["<table class='g'><tr><th>prompt</th>" + "".join(f"<th>{html.escape(a)}</th>" for a in arms) + "</tr>"]
    for pid in prompts:
        tds = []
        for a in arms:
            r = cells.get((pid, a))
            if r is None:
                tds.append("<td>·</td>")
                continue
            img = ""
            if r.sheet and Path(r.sheet).is_file():
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", a)   # an import arm's name carries a path
                dst = assets / f"{pid}__{safe}{Path(r.sheet).suffix}"
                shutil.copy2(r.sheet, dst)
                img = f"<img src='report_assets/{dst.name}' loading='lazy'>"
            cls = "ok" if r.passed else "bad"
            tds.append(f"<td>{img}<b class='{cls}'>{_f(r.score, '.2f')}</b> {'pass' if r.passed else r.status} · "
                       f"tris {r.tris or '-'} · ${r.gen_cost_usd:.2f} · {cell_minutes(r):.1f} min"
                       f"{'<pre>' + html.escape(r.error[:300]) + '</pre>' if r.error else ''}</td>")
        grid.append(f"<tr><td><b>{html.escape(pid)}</b></td>{''.join(tds)}</tr>")
    grid.append("</table>")
    name = meta.get("battery", {}).get("name", out.name)
    return (f"<!doctype html><meta charset='utf-8'><title>compare {html.escape(name)}</title><style>{_STYLE}</style>"
            f"<h1>harness vs one-shot — {html.escape(name)}</h1><p>fixed judge {html.escape(str(meta.get('options', {}).get('judge')))}</p>"
            f"<table>{head}{summ}</table><h2>pairwise (A wins / B wins / ties)</h2><table><tr><th>A (harness)</th><th>B (one-shot)</th>"
            f"<th>w/l/t</th><th>A win rate</th></tr>{pw}</table><h2>gallery</h2>{''.join(grid)}")


def build_compare_report(out_dir: Path | str) -> tuple[str, str]:
    out = Path(out_dir)
    rows = list(latest(read_jsonl(out / "results.jsonl", CellResult)).values())
    pairs = list(latest(read_jsonl(out / "pairwise.jsonl", PairRow)).values())
    meta = json.loads((out / "matrix.json").read_text()) if (out / "matrix.json").is_file() else {}
    md = compare_markdown(out, rows, pairs, meta)
    page = compare_html(out, rows, pairs, meta)
    (out / "report.md").write_text(md)
    (out / "report.html").write_text(page)
    return md, page


__all__ = ["ArmStats", "CellResult", "PairRow", "PairStats", "arm_stats", "build_compare_report", "pair_stats"]
