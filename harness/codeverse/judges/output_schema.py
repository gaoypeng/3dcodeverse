"""The judge's wire format: a pydantic model built per rubric (+ acceptance ids).

The model forces the VLM to score EVERY visual criterion and to answer EVERY
acceptance item; ``overall`` is deliberately absent — it is computed in code.
Parsing is lenient about shape (dict-of-records or list-of-records, fenced
JSON in plain text) but strict about content: a missing criterion is a
``JudgeParseError`` so the caller can retry instead of scoring a hole as 0.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, create_model

from codeverse.contracts.judgment import ImprovementItem, JudgeIssue
from codeverse.judges.rubrics import Rubric


class JudgeParseError(ValueError):
    """The model's reply could not be turned into a complete JudgeOutput."""


class CriterionScore(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="0..1 against the anchors")
    evidence: str = Field(description="what you saw, citing VIEW labels / measurements")


class AcceptanceVerdict(BaseModel):
    verified: bool = Field(description="true only if the renders/measurements prove it")
    evidence: str = Field(default="", description="which view / number proves or disproves it")


class DefectVerdict(BaseModel):
    present: bool = Field(description="true only if the defect is VISIBLE in the images or stated by a gate/measurement")
    evidence: str = Field(default="", description="which image / tile / number shows it (or why it is absent)")


class JudgeOutput(BaseModel):
    """Provider-neutral parsed judge reply (criteria + acceptance + defect checklist as dicts)."""

    criteria: dict[str, CriterionScore]
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    issues: list[JudgeIssue] = Field(default_factory=list)
    improvement_plan: list[ImprovementItem] = Field(default_factory=list)
    acceptance: dict[str, AcceptanceVerdict] = Field(default_factory=dict)
    defects: dict[str, bool] = Field(default_factory=dict, description="checklist id → defect present")
    defect_evidence: dict[str, str] = Field(default_factory=dict)

    @property
    def scores(self) -> dict[str, float]:
        return {k: v.score for k, v in self.criteria.items()}

    @property
    def acceptance_bools(self) -> dict[str, bool]:
        return {k: v.verified for k, v in self.acceptance.items()}


def build_wire_model(rubric: Rubric, acceptance_ids: list[str]) -> type[BaseModel]:
    """A pydantic model whose ``criteria`` / ``acceptance`` objects have FIXED keys.

    Criterion ids are valid identifiers (enforced by the rubric).  Acceptance ids
    are arbitrary strings → fields ``acc_<i>`` aliased to the id.
    """
    crit_fields: dict[str, Any] = {
        c.id: (CriterionScore, Field(description=f"{c.label}: {c.description.strip()[:200]}"))
        for c in rubric.visual_criteria()
    }
    Criteria = create_model("Criteria", **crit_fields)
    # Field order = generation order for JSON-mode models: observe (summary, strengths, issues, defect
    # checklist, acceptance) BEFORE scoring, so the scores follow the evidence rather than the reverse.
    fields: dict[str, Any] = {
        "summary": (str, Field(description="2-4 sentences: what it is, what is right, what is most wrong")),
        "strengths": (list[str], Field(default_factory=list, description="≤ 5 short bullets")),
        "issues": (list[JudgeIssue], Field(default_factory=list, description="observable defects, most severe first")),
    }
    if rubric.defects:
        def_fields: dict[str, Any] = {
            d.id: (DefectVerdict, Field(description=d.text.strip()[:200])) for d in rubric.defects
        }
        Defects = create_model("Defects", **def_fields)
        fields["defects"] = (Defects, Field(description="binary checklist: one entry per defect id, present=true ONLY when seen"))
    if acceptance_ids:
        acc_fields: dict[str, Any] = {
            f"acc_{i}": (AcceptanceVerdict, Field(alias=aid, description=f"acceptance item {aid}"))
            for i, aid in enumerate(acceptance_ids)
        }
        Acceptance = create_model("Acceptance", **acc_fields)
        fields["acceptance"] = (Acceptance, Field(description="one entry per acceptance item id"))
    fields["criteria"] = (Criteria, Field(description="one entry per criterion id, scored AFTER the observations above"))
    fields["improvement_plan"] = (
        list[ImprovementItem],
        Field(default_factory=list, description="≤ 6 concrete instructions for the builder, priority 1 first"),
    )
    return create_model("JudgeReply", **fields)


def wire_schema(rubric: Rubric, acceptance_ids: list[str]) -> dict[str, Any]:
    return build_wire_model(rubric, acceptance_ids).model_json_schema(by_alias=True)


# --------------------------------------------------------------------------- parsing
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from a reply: raw, fenced, or the first balanced ``{...}`` block."""
    text = text.strip()
    for cand in (text, *(m.strip() for m in _FENCE.findall(text))):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise JudgeParseError(f"no JSON object in reply (first 200 chars): {text[:200]!r}")


def _records_to_dict(value: Any, key_names: tuple[str, ...]) -> dict[str, Any]:
    """Accept ``{id: {...}}`` or ``[{id: ..., ...}, ...]`` and return ``{id: {...}}``."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        out: dict[str, Any] = {}
        for rec in value:
            if not isinstance(rec, dict):
                raise JudgeParseError(f"record is not an object: {rec!r}")
            key = next((rec[k] for k in key_names if k in rec), None)
            if key is None:
                raise JudgeParseError(f"record lacks an id field {key_names}: {rec!r}")
            out[str(key)] = {k: v for k, v in rec.items() if k not in key_names}
        return out
    raise JudgeParseError(f"expected object or list, got {type(value).__name__}")


def _coerce_acceptance(value: Any) -> dict[str, Any]:
    recs = _records_to_dict(value, ("id", "item_id", "item"))
    out: dict[str, Any] = {}
    for k, v in recs.items():
        if isinstance(v, bool):
            out[k] = {"verified": v, "evidence": ""}
        elif isinstance(v, dict):
            vv = dict(v)
            if "verified" not in vv and "passed" in vv:
                vv["verified"] = vv.pop("passed")
            out[k] = vv
        else:
            raise JudgeParseError(f"acceptance item {k!r}: unexpected value {v!r}")
    return out


def _coerce_defects(value: Any) -> tuple[dict[str, bool], dict[str, str]]:
    """``{id: bool}`` or ``{id: {present, evidence}}`` or a list of records → (present, evidence)."""
    if value is None:
        return {}, {}
    recs = _records_to_dict(value, ("id", "defect", "item"))
    present: dict[str, bool] = {}
    evidence: dict[str, str] = {}
    for k, v in recs.items():
        if isinstance(v, bool):
            present[k] = v
        elif isinstance(v, dict):
            flag = v.get("present", v.get("value", v.get("verified")))
            if not isinstance(flag, bool):
                raise JudgeParseError(f"defect {k!r}: 'present' must be a boolean, got {flag!r}")
            present[k] = flag
            evidence[k] = str(v.get("evidence", "") or "")
        else:
            raise JudgeParseError(f"defect item {k!r}: unexpected value {v!r}")
    return present, evidence


def parse_judge_output(
    payload: Any, rubric: Rubric, acceptance_ids: list[str], *, measured_scores: dict[str, float] | None = None
) -> JudgeOutput:
    """Validate a parsed reply (dict) or raw text into a complete ``JudgeOutput``.

    ``measured_scores`` (criterion id → score) are injected for ``kind: measured``
    criteria so the output always covers the full rubric.
    """
    if isinstance(payload, str):
        payload = extract_json(payload)
    if not isinstance(payload, dict):
        raise JudgeParseError(f"reply is not a JSON object: {type(payload).__name__}")
    data = dict(payload)
    if "criteria" not in data and "per_criterion" in data:
        data["criteria"] = data.pop("per_criterion")
    try:
        criteria = _records_to_dict(data.get("criteria", {}), ("id", "criterion", "name"))
        criteria = {
            k: ({"score": v, "evidence": ""} if isinstance(v, (int, float)) else v) for k, v in criteria.items()
        }
        acceptance = _coerce_acceptance(data.get("acceptance", {}))
        defects, defect_evidence = _coerce_defects(data.get("defects", {}))
        out = JudgeOutput(
            criteria=criteria,
            summary=str(data.get("summary", "") or ""),
            strengths=[str(s) for s in data.get("strengths", []) or []],
            issues=data.get("issues", []) or [],
            improvement_plan=data.get("improvement_plan", []) or [],
            acceptance=acceptance,
            defects=defects,
            defect_evidence=defect_evidence,
        )
    except ValidationError as e:
        raise JudgeParseError(f"judge reply failed validation: {e}") from e
    measured = measured_scores or {}
    for cid, s in measured.items():
        out.criteria[cid] = CriterionScore(score=max(0.0, min(1.0, float(s))), evidence="measured in code")
    missing = [c.id for c in rubric.criteria if c.id not in out.criteria]
    if missing:
        raise JudgeParseError(f"judge reply missing criteria {missing}")
    extra = [k for k in out.criteria if k not in rubric.weights]
    for k in extra:  # unknown criteria are ignored, not scored
        out.criteria.pop(k)
    missing_acc = [a for a in acceptance_ids if a not in out.acceptance]
    for a in missing_acc:  # unanswered acceptance items count as NOT verified
        out.acceptance[a] = AcceptanceVerdict(verified=False, evidence="not answered by judge")
    known = {d.id for d in rubric.defects}
    for k in [k for k in out.defects if k not in known]:  # unknown checklist ids are ignored
        out.defects.pop(k)
        out.defect_evidence.pop(k, None)
    for d in rubric.defects:
        if d.id not in out.defects:  # unanswered defect → absent (the conservative direction for a penalty)
            out.defects[d.id] = False
            out.defect_evidence[d.id] = "not answered by judge"
    return out
