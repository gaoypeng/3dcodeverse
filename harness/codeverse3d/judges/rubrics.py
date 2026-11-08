"""Rubrics: typed criteria, weights, floors, deterministic caps and a binary defect checklist, loaded from YAML.

A criterion is ``visual`` (scored by the VLM) or ``measured`` (scored in code, shown to the VLM).
``caps`` bound the overall score from gate findings; ``defects`` turn the VLM's present/absent answers
into a penalty and a cap.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import statistics
import typing
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from codeverse3d.contracts.artifacts import (
    GateFinding,
    GateReport,
    ImprovementItem,
    JudgeIssue,
    Judgment,
    RenderView,
    Severity,
)
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.jsonparse import JsonParseError, parse_json_lenient

RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"
ANCHOR_LEVELS = ("1.0", "0.7", "0.4", "0.1")
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


class RubricError(ValueError):
    """Raised for unknown / malformed rubrics."""


class Criterion(BaseModel):
    id: str
    weight: float = Field(gt=0.0, le=1.0)
    title: str = ""
    description: str = Field(description="what this criterion judges, one paragraph")
    anchors: dict[str, str] = Field(description="score level → what it looks like; keys 1.0/0.7/0.4/0.1")
    floor: float | None = Field(default=None, ge=0.0, le=1.0, description="hard floor: below → verdict fails")
    kind: Literal["visual", "measured"] = "visual"

    @field_validator("id")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError(f"criterion id must be snake_case identifier, got {v!r}")
        return v

    @field_validator("anchors")
    @classmethod
    def _anchors(cls, v: dict[str, str]) -> dict[str, str]:
        v = {str(k): str(t).strip() for k, t in v.items()}
        missing = [lvl for lvl in ANCHOR_LEVELS if lvl not in v]
        if missing:
            raise ValueError(f"anchors missing levels {missing}; need {ANCHOR_LEVELS}")
        return v

    @property
    def label(self) -> str:
        return self.title or self.id.replace("_", " ")


class DefectItem(BaseModel):
    """One binary checklist item; ``penalty`` is subtracted from the overall and ``cap`` bounds it when present."""

    id: str
    text: str = Field(description="concrete, visually checkable statement of the defect")
    penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    cap: float | None = Field(default=None, ge=0.0, le=1.0)
    @field_validator("id")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError(f"defect id must be snake_case identifier, got {v!r}")
        return v


class CapRule(BaseModel):
    """Bounds the overall score when a deterministic signal fires.

    ``when`` is ``gate`` (matches ``GateFinding`` by gate pattern, minimum severity and ``kinds``),
    ``acceptance`` (a ``must`` item unverified; ``graded`` scales the cap by verified/total),
    ``console`` or ``missing_views``.  ``measures`` names the checklist defects this rule measures.
    """

    id: str
    cap: float = Field(ge=0.0, le=1.0)
    when: Literal["gate", "acceptance", "console", "missing_views"] = "gate"
    gate: str = Field(default="*", description="fnmatch pattern on GateFinding.gate")
    severity: Severity = Severity.ERROR
    kinds: list[str] = Field(default_factory=list)
    measures: list[str] = Field(
        default_factory=list, description="checklist defect ids a passed gate under this rule vetoes; default [id]"
    )
    graded: bool = Field(
        default=False,
        description="acceptance rules only: cap + (1 - cap) * verified_must / total_must instead of the flat cap",
    )
    veto_warn_keys: dict[str, float] = Field(
        default_factory=dict,
        description="gate rules only: finding.data key → magnitude at or above which a WARN of the rule's kinds "
                    "keeps the judge's claim alive (measured_absent); empty = the penetration default, "
                    "{depth_m: VETO_PENETRATION_DEPTH_M}.  scene states {gap_m: 0.02} / {sunk_m: 0.10} / "
                    "{overlap: 0.20}; 0.0 = any WARN of the kind that carries the key",
    )
    @model_validator(mode="after")
    def _default_measures(self) -> CapRule:
        if not self.measures:
            self.measures = [self.id]
        return self


class Rubric(BaseModel):
    name: str
    version: int = 1
    pass_threshold: float = Field(ge=0.0, le=1.0)
    criteria: list[Criterion] = Field(min_length=1)
    caps: list[CapRule] = Field(default_factory=list)
    defects: list[DefectItem] = Field(default_factory=list, description="binary checklist scored in code")
    extra_instructions: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Rubric:
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate criterion ids in rubric {self.name}")
        total = sum(c.weight for c in self.criteria)
        if abs(total - 1.0) > 0.02:
            raise ValueError(f"rubric {self.name}: weights sum to {total:.3f}, expected 1.0")
        cap_ids = [c.id for c in self.caps]
        if len(cap_ids) != len(set(cap_ids)):
            raise ValueError(f"duplicate cap ids in rubric {self.name}")
        d_ids = [d.id for d in self.defects]
        if len(d_ids) != len(set(d_ids)):
            raise ValueError(f"duplicate defect ids in rubric {self.name}")
        return self

    @property
    def weights(self) -> dict[str, float]:
        return {c.id: c.weight for c in self.criteria}

    def defect(self, did: str) -> DefectItem:
        for d in self.defects:
            if d.id == did:
                return d
        raise KeyError(f"rubric {self.name} has no defect {did!r}")

    def visual_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.kind == "visual"]

    def measured_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.kind == "measured"]

    def weighted_overall(self, scores: dict[str, float]) -> float:
        """Σ w·s / Σ w over the rubric's criteria.  Every criterion must be present."""
        missing = [c.id for c in self.criteria if c.id not in scores]
        if missing:
            raise RubricError(f"cannot score rubric {self.name}: missing criteria {missing}")
        num = sum(c.weight * float(scores[c.id]) for c in self.criteria)
        den = sum(c.weight for c in self.criteria)
        return max(0.0, min(1.0, num / den))

    def floors_hit(self, scores: dict[str, float]) -> list[tuple[str, float, float]]:
        """(criterion id, score, floor) for every criterion scored below its floor."""
        out = []
        for c in self.criteria:
            if c.floor is not None and c.id in scores and float(scores[c.id]) < c.floor:
                out.append((c.id, float(scores[c.id]), c.floor))
        return out

    _source: dict[str, Any] | None = PrivateAttr(default=None)

    def content_hash(self) -> str:
        """A hash of what the YAML declares, not of the model's defaults, so a schema field never re-keys verdicts."""
        src = self._source if self._source is not None else self.model_dump(mode="json")
        payload = json.dumps(src, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def list_rubrics() -> list[str]:
    return sorted(p.stem for p in RUBRICS_DIR.glob("*.yaml"))


def rubric_from_dict(data: dict, *, name_hint: str = "") -> Rubric:
    try:
        rubric = Rubric(**data)
    except Exception as e:
        raise RubricError(f"invalid rubric {name_hint or data.get('name')!r}: {e}") from e
    rubric._source = json.loads(json.dumps(data, default=str))
    return rubric


@lru_cache(maxsize=32)
def load_rubric(name: str) -> Rubric:
    """Load ``rubrics/<name>.yaml`` (or a path to a yaml file) into a validated ``Rubric``."""
    path = Path(name)
    if not (path.suffix in (".yaml", ".yml") and path.is_file()):
        path = RUBRICS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise RubricError(f"unknown rubric {name!r}; available: {list_rubrics()}")
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise RubricError(f"rubric file {path} must be a mapping")
    data.setdefault("name", path.stem)
    return rubric_from_dict(data, name_hint=path.stem)


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
    evidence: str = Field(default="", description="which image / tile / number shows it; empty when absent")


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


def _object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JudgeParseError(f"{what} is not an object: {type(value).__name__}")
    return value


def _flatten_defects(value: Any) -> tuple[dict[str, bool], dict[str, str]]:
    """``{id: {present, evidence}}`` (the wire shape) → (present, evidence); ``present`` must be a real boolean."""
    present: dict[str, bool] = {}
    evidence: dict[str, str] = {}
    for k, v in _object(value, "defects").items():
        flag = v.get("present") if isinstance(v, dict) else None
        if not isinstance(flag, bool):
            raise JudgeParseError(f"defect {k!r}: 'present' must be a boolean, got {v!r}")
        present[k] = flag
        evidence[k] = str(v.get("evidence", "") or "")
    return present, evidence


def parse_judge_output(
    payload: Any, rubric: Rubric, acceptance_ids: list[str], *, measured_scores: dict[str, float] | None = None
) -> JudgeOutput:
    """Validate a parsed reply (dict) or raw text into a complete ``JudgeOutput``; ``measured_scores`` fill ``measured`` criteria."""
    if isinstance(payload, str):
        try:
            payload = parse_json_lenient(payload)
        except JsonParseError as e:
            raise JudgeParseError(f"no JSON object in reply: {e}") from e
    if not isinstance(payload, dict):
        raise JudgeParseError(f"reply is not a JSON object: {type(payload).__name__}")
    data = payload
    try:
        defects, defect_evidence = _flatten_defects(data.get("defects") or {})
        out = JudgeOutput(
            criteria=_object(data.get("criteria", {}), "criteria"),
            summary=str(data.get("summary", "") or ""),
            strengths=[str(s) for s in data.get("strengths", []) or []],
            issues=data.get("issues", []) or [],
            improvement_plan=data.get("improvement_plan", []) or [],
            acceptance=_object(data.get("acceptance", {}), "acceptance"),
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
    for k in extra:
        out.criteria.pop(k)
    out.acceptance = {a: out.acceptance.get(a) or AcceptanceVerdict(verified=False, evidence="not answered by judge")
                      for a in acceptance_ids}
    known = {d.id for d in rubric.defects}
    for k in [k for k in out.defects if k not in known]:
        out.defects.pop(k)
        out.defect_evidence.pop(k, None)
    for d in rubric.defects:
        if d.id not in out.defects:
            out.defects[d.id] = False
            out.defect_evidence[d.id] = "not answered by judge"
    return out


# ===================================================================== repair
def _literal_values(model: type[BaseModel], field: str) -> tuple[str, ...]:
    return tuple(str(v) for v in typing.get_args(model.model_fields[field].annotation))


def _bounds(model: type[BaseModel], field: str) -> tuple[float, float]:
    meta = model.model_fields[field].metadata
    return (next(float(m.ge) for m in meta if hasattr(m, "ge")), next(float(m.le) for m in meta if hasattr(m, "le")))


#: an issue kind the improvement enum lacks → the improvement kind the builder acts on
IMPROVEMENT_KIND_FALLBACK = {"detail": "geometry", "proportion": "geometry", "scale": "geometry", "fidelity": "composition"}
SEVERITY_FALLBACK = {"blocking": "critical", "severe": "critical", "high": "major", "medium": "major", "moderate": "major",
                     "low": "minor", "trivial": "minor", "cosmetic": "minor"}
#: how alike a misspelt enum value must be to the one it is read as
ENUM_MATCH_CUTOFF = 0.75
_TRUE_WORDS = {"true", "yes", "present", "verified", "1"}
_FALSE_WORDS = {"false", "no", "absent", "unverified", "0", "none", ""}


def _enum(value: Any, allowed: tuple[str, ...], synonyms: dict[str, str], default: str, where: str,
          repairs: list[str]) -> str:
    """``value`` read as one of ``allowed``: as written, a listed synonym, the closest spelling, else ``default``."""
    text = str(value if value is not None else "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in allowed:
        return text
    if text in synonyms and synonyms[text] in allowed:
        fixed = synonyms[text]
    else:
        close = difflib.get_close_matches(text, allowed, n=1, cutoff=ENUM_MATCH_CUTOFF)
        fixed = close[0] if close else default
    repairs.append(f"{where} {value!r} is not one of {list(allowed)} → read as {fixed!r}")
    return fixed


def _bool(value: Any, where: str, repairs: list[str]) -> bool | None:
    """A real boolean, a yes/no word or a 0/1 → bool; anything else None."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if isinstance(value, (str, int, float)) else None
    if text in _TRUE_WORDS or text in _FALSE_WORDS:
        flag = text in _TRUE_WORDS
        repairs.append(f"{where} {value!r} → {str(flag).lower()}")
        return flag
    return None


def _number(value: Any, lo: float, hi: float, where: str, repairs: list[str], *, percent: bool = False) -> float | None:
    """A number (or a numeric string) clamped to ``lo..hi``; ``percent`` reads 1 < x ≤ 100 as x/100.  None when not a number."""
    try:
        x = float(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        x = None
    if x is None or x != x:
        return None
    if percent and 1.0 < x <= 100.0:
        repairs.append(f"{where} {value!r} read as a percentage → {x / 100:.2f}")
        x = x / 100
    if not lo <= x <= hi:
        clamped = min(hi, max(lo, x))
        repairs.append(f"{where} {value!r} is outside {lo:g}..{hi:g} → {clamped:g}")
        x = clamped
    if isinstance(value, str):
        repairs.append(f"{where} {value!r} is a string → {x:g}")
    return x


def _keyed(section: Any, ids: list[str], flag: str, lo_hi: tuple[float, float] | None, where: str,
           repairs: list[str], missing: list[str], *, required: bool) -> dict[str, dict[str, Any]]:
    """criteria / defects / acceptance: one ``{flag, evidence}`` object per known id; bare values are wrapped, unknown
    ids dropped, absent ids reported as missing (``required``) or left to the parser's default."""
    raw = section if isinstance(section, dict) else {}
    if section is not None and not isinstance(section, dict):
        repairs.append(f"{where} is not an object → ignored")
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if key not in ids:
            repairs.append(f"{where}.{key} is not a known id → dropped")
            continue
        body = dict(value) if isinstance(value, dict) else {flag: value}
        if not isinstance(value, dict):
            repairs.append(f"{where}.{key} was a bare value → wrapped as {{{flag}, evidence}}")
        v = body.get(flag)
        fixed = _number(v, *lo_hi, f"{where}.{key}.{flag}", repairs, percent=True) if lo_hi else _bool(v, f"{where}.{key}.{flag}", repairs)
        if fixed is None:
            missing.append(f"{where}.{key}.{flag}" + (" (a number 0..1)" if lo_hi else " (true or false)"))
            continue
        body[flag] = fixed
        body["evidence"] = str(body.get("evidence") or "")
        out[key] = body
    for key in ids:
        if key not in out and not any(m.startswith(f"{where}.{key}.") for m in missing):
            if required:
                missing.append(f"{where}.{key}")
            else:
                repairs.append(f"{where}.{key} not answered → false")
    return out


def repair_judgment(payload: Any, rubric: Rubric, acceptance_ids: list[str], *,
                    measured_scores: dict[str, float] | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    """Mend the trivial deviations of a judge's JSON and name what is still missing.

    Returns ``(payload, repairs, missing)``: each repair is one line saying what was read as what; ``missing`` lists
    the keys the judge must add (a criterion score, a defect answer, an acceptance verdict) — those are never
    invented.  Enum values are read as the closest allowed one (``IMPROVEMENT_KIND_FALLBACK`` / ``SEVERITY_FALLBACK``
    first), numbers are clamped to their range, bare values are wrapped, unknown keys and ids are dropped.
    """
    repairs: list[str] = []
    missing: list[str] = []
    if not isinstance(payload, dict):
        return {}, repairs, ["the file must hold ONE JSON object"]
    data: dict[str, Any] = {}
    known = ("summary", "strengths", "issues", "defects", "acceptance", "criteria", "improvement_plan")
    for key in payload:
        if key not in known:
            repairs.append(f"{key!r} is not a key of the judgment → dropped")
    if not isinstance(payload.get("summary", ""), str):
        repairs.append("summary is not text → str()")
    data["summary"] = str(payload.get("summary") or "")
    strengths = payload.get("strengths") or []
    data["strengths"] = [str(x) for x in strengths] if isinstance(strengths, list) else []
    score_lo_hi = _bounds(CriterionScore, "score")
    visual_ids = [c.id for c in rubric.visual_criteria()]
    measured_ids = [c.id for c in rubric.measured_criteria() if c.id not in (measured_scores or {})]
    data["criteria"] = _keyed(payload.get("criteria"), visual_ids + measured_ids, "score", score_lo_hi, "criteria",
                              repairs, missing, required=True)
    data["defects"] = _keyed(payload.get("defects"), [d.id for d in rubric.defects], "present", None, "defects",
                             repairs, missing, required=True)
    # an absent defect needs no evidence; a present one drives penalties and caps, so it must say where it shows
    missing += [f"defects.{key}.evidence (present is true: one sentence saying where it is visible, or which gate ERROR / "
                "measurement states it)" for key, body in data["defects"].items() if body["present"] and not body["evidence"].strip()]
    data["acceptance"] = _keyed(payload.get("acceptance"), list(acceptance_ids), "verified", None, "acceptance",
                                repairs, missing, required=True)
    data["issues"] = _issues(payload.get("issues"), repairs)
    data["improvement_plan"] = _plan(payload.get("improvement_plan"), repairs)
    return data, repairs, missing


def _issues(raw: Any, repairs: list[str]) -> list[dict[str, Any]]:
    if raw is not None and not isinstance(raw, list):
        repairs.append("issues is not a list → ignored")
    out = []
    for i, item in enumerate(raw if isinstance(raw, list) else []):
        where = f"issues[{i}]"
        if not isinstance(item, dict):
            repairs.append(f"{where} is not an object → dropped")
            continue
        detail = str(item.get("detail") or item.get("instruction") or item.get("text") or item.get("evidence") or "")
        if not detail:
            repairs.append(f"{where} has no detail → dropped")
            continue
        out.append({
            "target": str(item.get("target") or "overall"),
            "kind": _enum(item.get("kind"), _literal_values(JudgeIssue, "kind"), {}, "geometry", f"{where}.kind", repairs),
            "severity": _enum(item.get("severity"), _literal_values(JudgeIssue, "severity"), SEVERITY_FALLBACK, "minor",
                              f"{where}.severity", repairs),
            "detail": detail, "evidence": str(item.get("evidence") or ""),
        })
    return out


def _plan(raw: Any, repairs: list[str]) -> list[dict[str, Any]]:
    if raw is not None and not isinstance(raw, list):
        repairs.append("improvement_plan is not a list → ignored")
    lo, hi = _bounds(ImprovementItem, "priority")
    out = []
    for i, item in enumerate(raw if isinstance(raw, list) else []):
        where = f"improvement_plan[{i}]"
        if not isinstance(item, dict):
            repairs.append(f"{where} is not an object → dropped")
            continue
        instruction = str(item.get("instruction") or item.get("detail") or item.get("text") or "")
        if not instruction:
            repairs.append(f"{where} has no instruction → dropped")
            continue
        priority = _number(item.get("priority"), lo, hi, f"{where}.priority", repairs)
        if priority is None:
            priority = min(hi, float(i + 1))
            repairs.append(f"{where}.priority {item.get('priority')!r} → {int(priority)} (its position)")
        gain = _number(item.get("expected_gain", 0.0), *_bounds(ImprovementItem, "expected_gain"), f"{where}.expected_gain", repairs)
        if gain is None:
            repairs.append(f"{where}.expected_gain {item.get('expected_gain')!r} → 0")
            gain = 0.0
        out.append({
            "target": str(item.get("target") or "overall"),
            "kind": _enum(item.get("kind"), _literal_values(ImprovementItem, "kind"), IMPROVEMENT_KIND_FALLBACK, "geometry",
                          f"{where}.kind", repairs),
            "instruction": instruction, "priority": int(round(priority)), "expected_gain": gain,
        })
    return out[:int(hi)] if len(out) <= hi else _cap_plan(out, int(hi), repairs)


def _cap_plan(items: list[dict[str, Any]], cap: int, repairs: list[str]) -> list[dict[str, Any]]:
    repairs.append(f"improvement_plan has {len(items)} items → the {cap} with the lowest priority numbers kept")
    return sorted(items, key=lambda it: it["priority"])[:cap]


_SEV_RANK = {Severity.INFO: 0, Severity.WARN: 1, Severity.ERROR: 2}


class CapApplied(BaseModel):
    rule: str
    cap: float
    evidence: str = Field(description="which finding / acceptance item triggered it")


class CapResult(BaseModel):
    overall: float
    caps_applied: list[CapApplied] = Field(default_factory=list)

    @property
    def capped(self) -> bool:
        return bool(self.caps_applied)


def _finding_tokens(f: GateFinding) -> str:
    """What a cap rule's ``kinds`` are matched against: the finding's ``data.kind`` / ``data.code``, and the
    message only if it has neither.  Never the target name."""
    typed = " ".join(str(f.data.get(k, "")) for k in ("kind", "code")).strip()
    return (typed or f.message).lower()


def _finding_matches(rule: CapRule, report: GateReport, f: GateFinding) -> bool:
    if not fnmatch(report.gate, rule.gate) and not fnmatch(f.gate, rule.gate):
        return False
    if _SEV_RANK[f.severity] < _SEV_RANK[rule.severity]:
        return False
    if rule.kinds:
        text = _finding_tokens(f)
        return any(k.lower() in text for k in rule.kinds)
    return True


def missing_must_items(
    acceptance_items: list[AcceptanceItem], acceptance_results: dict[str, bool]
) -> list[str]:
    """Ids of ``must`` acceptance items that are not verified (missing counts as failed)."""
    return [
        a.id
        for a in acceptance_items
        if a.priority == "must" and not acceptance_results.get(a.id, False)
    ]


def measured_absent(rubric: Rubric, defect_id: str, gates: list[GateReport]) -> bool:
    """True when a gate measured the thing this checklist defect claims and found nothing.

    The rule is the ``when="gate"`` cap rule whose ``measures`` names the defect; every gate it
    watches must have run and passed.  A defect with no such rule is never overridden.
    """
    rules = [r for r in rubric.caps if r.when == "gate" and defect_id in r.measures and r.kinds]
    if not rules:
        return False
    return all(_rule_gates_clean(r, gates) for r in rules)


#: a penetration WARN at least this deep is NOT "measured absent": the judge's reading stands
VETO_PENETRATION_DEPTH_M = 0.008


def _rule_gates_clean(rule: CapRule, gates: list[GateReport]) -> bool:
    """Every gate ``rule`` watches ran and reports no finding of its ``kinds`` that is an ERROR, or a WARN
    whose magnitude reaches the veto line.  A failed report naming no ERROR finding counts as not clean."""
    watched = [g for g in gates if fnmatch(g.gate, rule.gate) or any(fnmatch(f.gate, rule.gate) for f in g.findings)]
    if not watched:
        return False
    lines = rule.veto_warn_keys or {"depth_m": VETO_PENETRATION_DEPTH_M}
    for g in watched:
        if not g.passed and not any(f.severity == Severity.ERROR for f in g.findings):
            return False
        for f in g.findings:
            if not any(k.lower() in _finding_tokens(f) for k in rule.kinds):
                continue
            if f.severity == Severity.ERROR:
                return False
            if f.severity == Severity.WARN and _reaches_veto_line(f, lines):
                return False
    return True


def _reaches_veto_line(f: GateFinding, lines: dict[str, float]) -> bool:
    """Does a WARN's measured magnitude (any of ``lines``' keys in ``f.data``) reach its line?"""
    data = f.data if isinstance(f.data, dict) else {}
    for key, line in lines.items():
        v = data.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) >= line:
            return True
    return False


def veto_measured_defects(
    rubric: Rubric, defects: dict[str, bool], gates: list[GateReport]
) -> tuple[dict[str, bool], list[str]]:
    """``defects`` with every measured-absent one switched off, plus the ids switched off."""
    overridden = [did for did, on in defects.items() if on and measured_absent(rubric, did, gates)]
    return {did: (on and did not in overridden) for did, on in defects.items()}, overridden


def apply_caps(
    rubric: Rubric,
    overall: float,
    gates: list[GateReport],
    acceptance_results: dict[str, bool],
    acceptance_items: list[AcceptanceItem] | None = None,
    *,
    console_errors: list[str] | None = None,
    views: list[RenderView] | None = None,
    defects_present: dict[str, bool] | None = None,
) -> CapResult:
    """Apply every rubric cap rule; the overall becomes ``min(overall, caps...)``.

    One ledger line per rule (first matching finding) and one ``defect:<id>`` line per present capped defect.
    """
    applied: list[CapApplied] = []
    for rule in rubric.caps:
        hit = _rule_hit(
            rule,
            gates,
            acceptance_results,
            acceptance_items or [],
            console_errors or [],
            views or [],
        )
        if hit is not None:
            applied.append(hit)
    for did, present in (defects_present or {}).items():
        if not present:
            continue
        try:
            item = rubric.defect(did)
        except KeyError:
            continue
        if item.cap is not None:
            applied.append(
                CapApplied(
                    rule=f"defect:{did}",
                    cap=item.cap,
                    evidence=f"judge checklist: {item.text[:120]}",
                )
            )
    capped = overall
    for a in applied:
        capped = min(capped, a.cap)
    return CapResult(overall=max(0.0, min(1.0, capped)), caps_applied=applied)


def _rule_hit(
    rule: CapRule,
    gates: list[GateReport],
    acceptance_results: dict[str, bool],
    acceptance_items: list[AcceptanceItem],
    console_errors: list[str],
    views: list[RenderView],
) -> CapApplied | None:
    """The ledger line ``rule`` earns on this evidence, or None when it does not fire."""
    if rule.when == "missing_views":
        tokens = [k.lower() for k in rule.kinds]
        if any(any(t in f"{v.name} {v.mode}".lower() for t in tokens) for v in views):
            return None
        return CapApplied(rule=rule.id, cap=rule.cap,
                          evidence=f"no render view matching {rule.kinds} among {len(views)} view(s)")
    if rule.when == "acceptance":
        missing = missing_must_items(acceptance_items, acceptance_results)
        if not missing:
            return None
        total = sum(1 for a in acceptance_items if a.priority == "must")
        verified = total - len(missing)
        cap = rule.cap + (1.0 - rule.cap) * verified / total if rule.graded and total else rule.cap
        return CapApplied(rule=rule.id, cap=cap,
                          evidence=f"{verified} of {total} must items verified; not verified: {', '.join(missing[:6])}")
    if rule.when == "console":
        if console_errors:
            return CapApplied(rule=rule.id, cap=rule.cap,
                              evidence=f"{len(console_errors)} console error(s); first: {console_errors[0][:160]}")
        return None
    for report in gates:
        for f in report.findings:
            if _finding_matches(rule, report, f):
                target = f" [{f.target}]" if f.target else ""
                return CapApplied(rule=rule.id, cap=rule.cap, evidence=f"{report.gate}{target}: {f.message[:200]}")
    return None


SCORING_VERSION = 4
"""The arithmetic downstream of the model, stamped into every ``ScoreBreakdown``.

A stored verdict is reproducible from its samples only under the version that wrote
it; an older stamp is drift to report, not a fault.

Versions: 1 veto of checklist defects a passed gate measured absent · 2 ``CapRule.measures``, a defect
vote tie is absent, graded ``missing_must_acceptance`` · 3 cap ``kinds`` match the typed ``data.kind`` /
``data.code`` · 4 the veto is scoped to the rule's ``kinds`` and its WARN magnitude is ``veto_warn_keys``.
"""


class ScoreBreakdown(BaseModel):
    """Everything the pass/fail decision used (serialised into ``Judgment.raw``)."""

    status: str = Field(default="ok", description="ok | degraded")
    scoring_version: int = Field(default=0, description="SCORING_VERSION that produced this breakdown")
    per_criterion_std: dict[str, float]
    overall_uncapped: float = Field(
        description="rubric-weighted mean of the per-criterion means, before defects/caps"
    )
    overall: float
    caps: CapResult
    defects: dict[str, bool] = Field(
        default_factory=dict, description="checklist id → present (majority vote)"
    )
    overridden: list[str] = Field(default_factory=list,
                                  description="checklist defects the vote marked present that a passed gate measured absent (veto_measured_defects)")
    defect_penalty: float = 0.0
    n_requested: int
    sample_errors: list[str] = Field(default_factory=list)
    rubric_hash: str = ""
    judge_prompt_hash: str = Field(default="", description="hash of everything the judge is told that is constant per rubric")
    samples: list[dict[str, Any]] = Field(default_factory=list)


def _std(vals: list[float]) -> float:
    return float(statistics.pstdev(vals)) if len(vals) > 1 else 0.0


def _representative(samples: list[JudgeOutput], overalls: list[float]) -> JudgeOutput:
    mean = statistics.fmean(overalls)
    return min(zip(samples, overalls, strict=True), key=lambda so: abs(so[1] - mean))[0]


def _vote(votes: list[bool], tie_break: bool) -> bool:
    """Majority of ``votes``; an exact tie takes ``tie_break``.

    Acceptance ties take the representative sample; defect ties take ``False`` (the defect bears the burden).
    """
    if not votes:
        return False
    yes = sum(1 for v in votes if v)
    return bool(tie_break) if yes * 2 == len(votes) else yes * 2 > len(votes)


def aggregate_samples(
    rubric: Rubric,
    samples: list[JudgeOutput],
    *,
    gates: list[GateReport],
    acceptance_items: list[AcceptanceItem],
    console_errors: list[str] | None = None,
    views: list[RenderView] | None = None,
    judge_backend: str = "",
    n_requested: int | None = None,
    sample_errors: list[str] | None = None,
    judge_prompt_hash: str = "",
) -> Judgment:
    """Combine ≥ 1 parsed samples into a Judgment (raises ValueError on zero); ``views`` feed ``missing_views`` caps."""
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
        {"criterion": cid, "score": round(sc, 4), "floor": fl}
        for cid, sc, fl in rubric.floors_hit(mean_scores)
    ]
    rep = _representative(samples, overalls)
    votes: dict[str, list[bool]] = {}
    for s in samples:
        for aid, ok in s.acceptance_bools.items():
            votes.setdefault(aid, []).append(bool(ok))
    for a in acceptance_items:
        votes.setdefault(a.id, [False])
    acceptance: dict[str, bool] = {}
    for aid, v in votes.items():
        acceptance[aid] = _vote(v, bool(rep.acceptance_bools.get(aid, False)))
    must_missing = missing_must_items(acceptance_items, acceptance)

    d_votes: dict[str, list[bool]] = {d.id: [] for d in rubric.defects}
    for s in samples:
        for did, flag in s.defects.items():
            d_votes.setdefault(did, []).append(bool(flag))
    defects: dict[str, bool] = {}
    for did, v in d_votes.items():
        defects[did] = _vote(v, False)
    # a defect a passed gate measured as absent neither penalises nor caps (``veto_measured_defects``)
    defects, overridden = veto_measured_defects(rubric, defects, gates)
    penalty = round(sum(rubric.defect(did).penalty for did, on in defects.items() if on), 4)
    after_defects = max(0.0, overall_uncapped - penalty)

    caps = apply_caps(
        rubric,
        after_defects,
        gates,
        acceptance,
        acceptance_items,
        console_errors=console_errors,
        views=views,
        defects_present=defects,
    )
    overall = caps.overall
    passed = overall >= rubric.pass_threshold and not floors and not must_missing

    summary = rep.summary.strip()
    tail = _verdict_tail(
        rubric,
        overall,
        passed,
        floors,
        caps,
        must_missing,
        defects=defects,
        penalty=penalty,
        overridden=overridden,
    )
    if tail:
        summary = (summary + " " if summary else "") + tail

    breakdown = ScoreBreakdown(
        scoring_version=SCORING_VERSION,
        per_criterion_std=std_scores,
        overall_uncapped=round(overall_uncapped, 4),
        overall=round(overall, 4),
        caps=caps,
        defects=defects,
        overridden=overridden,
        defect_penalty=penalty,
        n_requested=n_requested or len(samples),
        sample_errors=sample_errors or [],
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
        raw=json.dumps(breakdown.model_dump(mode="json"), ensure_ascii=False),
    )


def _verdict_tail(
    rubric: Rubric,
    overall: float,
    passed: bool,
    floors: list[dict[str, Any]],
    caps: CapResult,
    must_missing: list[str],
    *,
    defects: dict[str, bool] | None = None,
    penalty: float = 0.0,
    overridden: list[str] | None = None,
) -> str:
    bits = [
        f"[verdict: overall {overall:.2f} vs threshold {rubric.pass_threshold:.2f} → {'PASS' if passed else 'FAIL'}"
    ]
    present = [d for d, on in (defects or {}).items() if on]
    if present:
        bits.append(f"defects (-{penalty:.2f}): " + ", ".join(present))
    if overridden:
        bits.append(
            "checklist claims contradicted by a passed gate (no penalty, no cap): "
            + ", ".join(overridden)
        )
    if caps.caps_applied:
        bits.append(
            "caps: " + "; ".join(f"{c.rule}≤{c.cap:.2f} ({c.evidence})" for c in caps.caps_applied)
        )
    if floors:
        bits.append(
            "floors: "
            + "; ".join(f"{f['criterion']} {f['score']:.2f}<{f['floor']:.2f}" for f in floors)
        )
    if must_missing:
        bits.append("must items unverified: " + ", ".join(must_missing))
    return " | ".join(bits) + "]"
