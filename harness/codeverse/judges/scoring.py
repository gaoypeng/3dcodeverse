"""Turn N parsed judge samples into ONE ``Judgment`` — all arithmetic in code.

* per-criterion score = mean over samples; overall = rubric-weighted mean;
  ``score_std`` = std of the per-sample overalls;
* floors: any criterion mean below its floor → verdict fails;
* defects: the rubric's binary checklist, majority vote over samples; each
  present item subtracts its ``penalty`` from the overall and its ``cap`` (if
  any) bounds it — arithmetic the VLM never does;
* caps: deterministic bounds from gate findings / acceptance / missing views
  (``caps.py``) plus the defect caps;
* acceptance: majority vote over samples;
* an EXACT tie on a binary item (only possible with an even ``n_samples``) follows
  the *representative* sample (below) instead of a fixed direction.  The old rule
  (defect ties → present, acceptance ties → False) made ``n_samples=2`` strictly
  harsher than both n=1 and n=3 — one dissenting sample applied every penalty and
  failed every must item — so scores from the ``economy`` profile (n=2) were not
  comparable with the others.  With the representative rule the probability that
  an item is flagged at n=2 equals n=1's, while the continuous scores still average
  over both samples; ids decided this way are listed in ``tie_broken``;
* narrative (summary / issues / plan / strengths) is taken from the sample whose
  overall is closest to the mean (the *representative* sample) so it is coherent.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport, RenderView
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
    overall_uncapped: float = Field(description="rubric-weighted mean of the per-criterion means, before defects/caps")
    overall_after_defects: float = Field(default=0.0, description="overall_uncapped minus defect penalties")
    overall: float
    floors_hit: list[dict[str, Any]] = Field(default_factory=list)
    caps: CapResult
    must_missing: list[str] = Field(default_factory=list)
    acceptance_votes: dict[str, list[bool]] = Field(default_factory=dict)
    defects: dict[str, bool] = Field(default_factory=dict, description="checklist id → present (majority vote)")
    defect_votes: dict[str, list[bool]] = Field(default_factory=dict)
    defect_penalty: float = 0.0
    n_requested: int
    n_used: int
    sample_errors: list[str] = Field(default_factory=list)
    tie_broken: list[str] = Field(default_factory=list,
                                  description="defect / acceptance ids an exact vote tie handed to the representative sample")
    rubric_hash: str = ""
    judge_prompt_hash: str = Field(default="", description="hash of everything the judge is told that is constant per rubric: "
                                                           "role prompt + view-rig rules + wire schema (prompt_builder.judge_prompt_hash)")
    samples: list[dict[str, Any]] = Field(default_factory=list)


def _std(vals: list[float]) -> float:
    return float(statistics.pstdev(vals)) if len(vals) > 1 else 0.0


def _representative(samples: list[JudgeOutput], overalls: list[float]) -> JudgeOutput:
    mean = statistics.fmean(overalls)
    return min(zip(samples, overalls, strict=True), key=lambda so: abs(so[1] - mean))[0]


def _vote(votes: list[bool], tie_break: bool) -> tuple[bool, bool]:
    """Majority of ``votes``; an exact tie takes ``tie_break``.  Returns (decision, was_tie)."""
    if not votes:
        return False, False
    yes = sum(1 for v in votes if v)
    if yes * 2 == len(votes):
        return bool(tie_break), True
    return yes * 2 > len(votes), False


def aggregate_samples(
    rubric: Rubric,
    samples: list[JudgeOutput],
    *,
    gates: list[GateReport],
    acceptance_items: list[AcceptanceItem],
    console_errors: list[str] | None = None,
    views: list[RenderView] | None = None,
    usage: Usage | None = None,
    judge_backend: str = "",
    n_requested: int | None = None,
    sample_errors: list[str] | None = None,
    judge_prompt_hash: str = "",
) -> Judgment:
    """Combine ≥ 1 parsed samples into a Judgment (raises ValueError on zero samples).

    ``views`` feeds ``missing_views`` cap rules (e.g. the articulated rubric's
    required pose sheet).
    """
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
    rep = _representative(samples, overalls)
    tie_broken: list[str] = []
    votes: dict[str, list[bool]] = {}
    for s in samples:
        for aid, ok in s.acceptance_bools.items():
            votes.setdefault(aid, []).append(bool(ok))
    for a in acceptance_items:
        votes.setdefault(a.id, [False])
    acceptance: dict[str, bool] = {}
    for aid, v in votes.items():
        acceptance[aid], tied = _vote(v, bool(rep.acceptance_bools.get(aid, False)))
        if tied:
            tie_broken.append(aid)
    must_missing = missing_must_items(acceptance_items, acceptance)

    d_votes: dict[str, list[bool]] = {d.id: [] for d in rubric.defects}
    for s in samples:
        for did, flag in s.defects.items():
            d_votes.setdefault(did, []).append(bool(flag))
    defects: dict[str, bool] = {}
    for did, v in d_votes.items():
        defects[did], tied = _vote(v, bool(rep.defects.get(did, False)))
        if tied:
            tie_broken.append(did)
    penalty = round(sum(rubric.defect(did).penalty for did, on in defects.items() if on), 4)
    after_defects = max(0.0, overall_uncapped - penalty)

    caps = apply_caps(rubric, after_defects, gates, acceptance, acceptance_items, console_errors=console_errors,
                      views=views, defects_present=defects)
    overall = caps.overall
    passed = overall >= rubric.pass_threshold and not floors and not must_missing

    summary = rep.summary.strip()
    tail = _verdict_tail(rubric, overall, passed, floors, caps, must_missing, defects=defects, penalty=penalty)
    if tail:
        summary = (summary + " " if summary else "") + tail

    breakdown = ScoreBreakdown(
        per_criterion_mean=mean_scores,
        per_criterion_std=std_scores,
        per_sample_overall=[round(o, 4) for o in overalls],
        overall_uncapped=round(overall_uncapped, 4),
        overall_after_defects=round(after_defects, 4),
        overall=round(overall, 4),
        floors_hit=floors,
        caps=caps,
        must_missing=must_missing,
        acceptance_votes=votes,
        defects=defects,
        defect_votes=d_votes,
        defect_penalty=penalty,
        n_requested=n_requested or len(samples),
        n_used=len(samples),
        sample_errors=sample_errors or [],
        tie_broken=tie_broken,
        rubric_hash=rubric.content_hash(),
        judge_prompt_hash=judge_prompt_hash,
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
    rubric: Rubric, overall: float, passed: bool, floors: list[dict[str, Any]], caps: CapResult, must_missing: list[str],
    *, defects: dict[str, bool] | None = None, penalty: float = 0.0,
) -> str:
    bits = [f"[verdict: overall {overall:.2f} vs threshold {rubric.pass_threshold:.2f} → {'PASS' if passed else 'FAIL'}"]
    present = [d for d, on in (defects or {}).items() if on]
    if present:
        bits.append(f"defects (-{penalty:.2f}): " + ", ".join(present))
    if caps.caps_applied:
        bits.append("caps: " + "; ".join(f"{c.rule}≤{c.cap:.2f} ({c.evidence})" for c in caps.caps_applied))
    if floors:
        bits.append("floors: " + "; ".join(f"{f['criterion']} {f['score']:.2f}<{f['floor']:.2f}" for f in floors))
    if must_missing:
        bits.append("must items unverified: " + ", ".join(must_missing))
    return " | ".join(bits) + "]"


def degraded_judgment(rubric: Rubric, error: str, *, usage: Usage, judge_backend: str, n_requested: int,
                      judge_prompt_hash: str = "") -> Judgment:
    """A verdict the orchestrator must treat as a GLITCH (not a score): ``raw.status == 'degraded'``."""
    breakdown = {
        "status": "degraded",
        "error": error,
        "n_requested": n_requested,
        "n_used": 0,
        "rubric_hash": rubric.content_hash(),
        "judge_prompt_hash": judge_prompt_hash,
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
