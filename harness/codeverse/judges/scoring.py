"""Turn N parsed judge samples into ONE ``Judgment`` — all arithmetic in code.

* per-criterion score = mean over samples; overall = rubric-weighted mean;
  ``score_std`` = std of the per-sample overalls;
* floors: any criterion mean below its floor → verdict fails;
* caps: deterministic bounds from gate findings / acceptance (``caps.py``);
* acceptance: majority vote over samples (ties → False);
* narrative (summary / issues / plan / strengths) is taken from the sample whose
  overall is closest to the mean (the *representative* sample) so it is coherent.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport
from codeverse.contracts.common import Usage
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem
from codeverse.judges.caps import CapResult, apply_caps, missing_must_items
from codeverse.judges.output_schema import JudgeOutput
from codeverse.judges.rubrics import Rubric


class ScoreBreakdown(BaseModel):
    """Everything the pass/fail decision used (serialised into ``Judgment.raw``)."""

    status: str = Field(default="ok", description="ok | degraded")
    per_criterion_mean: dict[str, float]
    per_criterion_std: dict[str, float]
    per_sample_overall: list[float]
    overall_uncapped: float
    overall: float
    floors_hit: list[dict[str, Any]] = Field(default_factory=list)
    caps: CapResult
    must_missing: list[str] = Field(default_factory=list)
    acceptance_votes: dict[str, list[bool]] = Field(default_factory=dict)
    n_requested: int
    n_used: int
    sample_errors: list[str] = Field(default_factory=list)
    rubric_hash: str = ""
    samples: list[dict[str, Any]] = Field(default_factory=list)


def _std(vals: list[float]) -> float:
    return float(statistics.pstdev(vals)) if len(vals) > 1 else 0.0


def _representative(samples: list[JudgeOutput], overalls: list[float]) -> JudgeOutput:
    mean = statistics.fmean(overalls)
    return min(zip(samples, overalls, strict=True), key=lambda so: abs(so[1] - mean))[0]


def aggregate_samples(
    rubric: Rubric,
    samples: list[JudgeOutput],
    *,
    gates: list[GateReport],
    acceptance_items: list[AcceptanceItem],
    console_errors: list[str] | None = None,
    usage: Usage | None = None,
    judge_backend: str = "",
    n_requested: int | None = None,
    sample_errors: list[str] | None = None,
) -> Judgment:
    """Combine ≥ 1 parsed samples into a Judgment (raises ValueError on zero samples)."""
    if not samples:
        raise ValueError("aggregate_samples needs at least one sample")
    per_crit: dict[str, list[float]] = {c.id: [] for c in rubric.criteria}
    overalls: list[float] = []
    for s in samples:
        sc = s.scores
        for cid in per_crit:
            per_crit[cid].append(float(sc[cid]))
        overalls.append(rubric.weighted_overall(sc))
    mean_scores = {cid: round(statistics.fmean(v), 4) for cid, v in per_crit.items()}
    std_scores = {cid: round(_std(v), 4) for cid, v in per_crit.items()}
    overall_uncapped = rubric.weighted_overall(mean_scores)

    floors = [
        {"criterion": cid, "score": round(sc, 4), "floor": fl} for cid, sc, fl in rubric.floors_hit(mean_scores)
    ]
    votes: dict[str, list[bool]] = {}
    for s in samples:
        for aid, ok in s.acceptance_bools.items():
            votes.setdefault(aid, []).append(bool(ok))
    for a in acceptance_items:
        votes.setdefault(a.id, [False])
    acceptance = {aid: (sum(v) * 2 > len(v)) for aid, v in votes.items()}
    must_missing = missing_must_items(acceptance_items, acceptance)

    caps = apply_caps(rubric, overall_uncapped, gates, acceptance, acceptance_items, console_errors=console_errors)
    overall = caps.overall
    passed = overall >= rubric.pass_threshold and not floors and not must_missing

    rep = _representative(samples, overalls)
    summary = rep.summary.strip()
    tail = _verdict_tail(rubric, overall, passed, floors, caps, must_missing)
    if tail:
        summary = (summary + " " if summary else "") + tail

    breakdown = ScoreBreakdown(
        per_criterion_mean=mean_scores,
        per_criterion_std=std_scores,
        per_sample_overall=[round(o, 4) for o in overalls],
        overall_uncapped=round(overall_uncapped, 4),
        overall=round(overall, 4),
        floors_hit=floors,
        caps=caps,
        must_missing=must_missing,
        acceptance_votes=votes,
        n_requested=n_requested or len(samples),
        n_used=len(samples),
        sample_errors=sample_errors or [],
        rubric_hash=rubric.content_hash(),
        samples=[s.model_dump(mode="json") for s in samples],
    )
    return Judgment(
        rubric=rubric.name,
        judge_backend=judge_backend,
        scores=mean_scores,
        overall=round(overall, 4),
        passed=passed,
        summary=summary,
        strengths=list(rep.strengths),
        issues=list(rep.issues),
        improvement_plan=sorted(rep.improvement_plan, key=lambda it: it.priority),
        acceptance_results=acceptance,
        n_samples=len(samples),
        score_std=round(_std(overalls), 4),
        usage=usage or Usage(),
        raw=json.dumps(breakdown.model_dump(mode="json"), ensure_ascii=False),
    )


def _verdict_tail(
    rubric: Rubric, overall: float, passed: bool, floors: list[dict[str, Any]], caps: CapResult, must_missing: list[str]
) -> str:
    bits = [f"[verdict: overall {overall:.2f} vs threshold {rubric.pass_threshold:.2f} → {'PASS' if passed else 'FAIL'}"]
    if caps.caps_applied:
        bits.append("caps: " + "; ".join(f"{c.rule}≤{c.cap:.2f} ({c.evidence})" for c in caps.caps_applied))
    if floors:
        bits.append("floors: " + "; ".join(f"{f['criterion']} {f['score']:.2f}<{f['floor']:.2f}" for f in floors))
    if must_missing:
        bits.append("must items unverified: " + ", ".join(must_missing))
    return " | ".join(bits) + "]"


def degraded_judgment(rubric: Rubric, error: str, *, usage: Usage, judge_backend: str, n_requested: int) -> Judgment:
    """A verdict the orchestrator must treat as a GLITCH (not a score): ``raw.status == 'degraded'``."""
    breakdown = {
        "status": "degraded",
        "error": error,
        "n_requested": n_requested,
        "n_used": 0,
        "rubric_hash": rubric.content_hash(),
    }
    return Judgment(
        rubric=rubric.name,
        judge_backend=judge_backend,
        scores={c.id: 0.0 for c in rubric.criteria},
        overall=0.0,
        passed=False,
        summary=f"judge_error: {error}",
        n_samples=0,
        usage=usage,
        raw=json.dumps(breakdown, ensure_ascii=False),
    )


def is_degraded(j: Judgment) -> bool:
    """True when a Judgment is a judge glitch rather than a score."""
    if j.summary.startswith("judge_error:"):
        return True
    try:
        return json.loads(j.raw).get("status") == "degraded"
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False
