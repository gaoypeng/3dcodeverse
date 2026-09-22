"""Rubrics: typed criteria + weights + floors + deterministic caps, loaded from YAML.

A rubric is data, not prose: every criterion carries *anchored* descriptions
(what 1.0 / 0.7 / 0.4 / 0.1 look like) so two judges read the same scale, an
optional hard ``floor`` (a criterion below its floor fails the verdict no matter
the weighted mean) and a ``kind`` (``visual`` = scored by the VLM,
``measured`` = scored in code and merely *shown* to the VLM).

``caps`` are rules that bound the overall score from deterministic gate
findings (build error → 0.0, floating part → ≤ 0.6 ...) — see ``apply_caps`` below.

``defects`` is a fixed **binary checklist** the VLM answers (present / absent)
— "a part floats", "lying on its back", "flat untextured" … — and the code
turns into arithmetic: ``penalty`` is subtracted from the weighted overall and
``cap`` bounds it.  Binary items survive the compressed dynamic range of
flash-class judges far better than 0..1 scores do.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
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
    create_model,
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
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.models.schema_utils import JsonParseError, parse_json_lenient

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
    """One binary checklist item the judge answers; scored in code.

    ``penalty`` is subtracted from the weighted overall when the defect is
    present (majority vote over samples); ``cap`` bounds the overall.  Either may
    be absent (a purely informational item has penalty 0 and no cap).
    """

    id: str
    text: str = Field(description="concrete, visually checkable statement of the defect")
    penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    cap: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str = ""

    @field_validator("id")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError(f"defect id must be snake_case identifier, got {v!r}")
        return v


class CapRule(BaseModel):
    """Bounds the overall score when a deterministic signal fires.

    ``when="gate"`` rules match ``GateFinding`` records: ``gate`` is an fnmatch
    pattern on the gate name (``"build*"``), ``severity`` the minimum severity,
    ``kinds`` a list of tokens any of which must appear in ``finding.data.kind``
    / ``finding.data.code`` or, as a fallback, in the lower-cased message.
    ``when="acceptance"`` fires when any ``must`` acceptance item is not verified;
    with ``graded`` the cap is ``cap + (1 - cap) · verified/total`` over the must
    items instead of the flat ``cap``.
    ``when="console"`` fires when the render set reports console errors (scenes).
    ``when="missing_views"`` fires when NO render view has a name/mode containing
    any of ``kinds`` (e.g. ``kinds: [pose_, articulation_sheet]`` = the articulated
    rubric requires posed views).

    ``measures`` names the checklist defects this gate rule is the MEASUREMENT of
    (default: the rule's own id).  ``measured_absent`` reads it: a passed gate the
    rule watches vetoes those checklist claims.  Audited 2026-08-30 over 420
    static_object verdicts: ``penetration_error`` had never vetoed ``interpenetration``
    because the match was on ``id`` alone — 237 interpenetration flags, 69 with a gate
    ERROR behind them, 0 vetoes.
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
    note: str = ""

    @model_validator(mode="after")
    def _default_measures(self) -> CapRule:
        if not self.measures:
            self.measures = [self.id]
        return self


class Rubric(BaseModel):
    name: str
    version: int = 1
    track_hint: str = Field(default="", description="static_object | articulated_object | scene | asset | reference")
    pass_threshold: float = Field(ge=0.0, le=1.0)
    criteria: list[Criterion] = Field(min_length=1)
    caps: list[CapRule] = Field(default_factory=list)
    defects: list[DefectItem] = Field(default_factory=list, description="binary checklist scored in code")
    extra_instructions: str = ""
    description: str = ""

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

    # ------------------------------------------------------------------ helpers
    @property
    def weights(self) -> dict[str, float]:
        return {c.id: c.weight for c in self.criteria}

    def criterion(self, cid: str) -> Criterion:
        for c in self.criteria:
            if c.id == cid:
                return c
        raise KeyError(f"rubric {self.name} has no criterion {cid!r}")

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
        """A hash of what the YAML DECLARES, not of the model's defaults.

        Hashing ``model_dump`` moved the hash of every rubric — scene_v1, shader_v1/v2
        included — when ``CapRule`` gained ``measures`` and ``graded`` (2026-08-30),
        although their files had not changed a byte; a schema field must never re-key
        recorded verdicts.  A rubric built without a source dict (tests) hashes its dump."""
        src = self._source if self._source is not None else self.model_dump(mode="json")
        payload = json.dumps(src, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def list_rubrics() -> list[str]:
    return sorted(p.stem for p in RUBRICS_DIR.glob("*.yaml"))


def rubric_from_dict(data: dict, *, name_hint: str = "") -> Rubric:
    try:
        rubric = Rubric(**data)
    except Exception as e:  # pydantic ValidationError / TypeError
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


# ===================================================================== output_schema
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
        try:
            payload = parse_json_lenient(payload)
        except JsonParseError as e:
            raise JudgeParseError(f"no JSON object in reply: {e}") from e
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


# ===================================================================== caps
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
    """Searchable lower-cased text for a finding: data.kind / data.code / message / target."""
    bits = [str(f.data.get("kind", "")), str(f.data.get("code", "")), f.message, f.target or ""]
    return " ".join(bits).lower()


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
    """True when a deterministic gate MEASURED the thing this checklist defect claims, and found nothing.

    A checklist defect is a VLM's reading of a picture.  Some of them — a floating part, an
    interpenetration — are also what a gate measures on the exported mesh, and the rubric
    already says which: a ``when="gate"`` cap rule whose ``measures`` names the defect and
    whose ``kinds`` name the findings.  When every gate that rule watches ran and passed
    with no ERROR finding of those kinds, the measurement contradicts the perception, and
    the perception must not cap.

    Measured 2026-08-26 on a plan-pinned pair (fancy_v1 gas_street_lamp): the connectivity
    gate said "all 9 parts connected, gap <= 2 mm"; the judge read the dark seam under the
    pedestal as "floating in mid-air, a clear daylight gap", marked floating_part present, and
    ``defect:floating_part`` capped the run at 0.6 (uncapped 0.72) against 0.96 for a sibling
    the eye cannot tell apart.  CLAUDE.md law 3: gates decide geometry, the VLM is perception.

    Audited 2026-08-30 (420 static_object verdicts): the veto had fired 10 times.  It matched
    ``r.id == defect_id`` — ``penetration_error`` never reached ``interpenetration`` — and the
    rules watched ``gate: "*"``, so a failing contract gate (53 of the 62 blocked cases)
    switched off a veto the connectivity gate had earned.  Now ``measures`` carries the
    defect ids and the object rubrics watch ``connectivity`` alone.

    A defect with no matching gate rule (wrong_object, missing_named_part …) is never
    overridden: nothing measured it.  A gate that did not run overrides nothing either.
    When several rules measure one defect, every one of them must be clean.
    """
    rules = [r for r in rubric.caps if r.when == "gate" and defect_id in r.measures and r.kinds]
    if not rules:
        return False
    return all(_rule_gates_clean(r, gates) for r in rules)


#: A penetration the gate rated WARN but measured at least this deep is NOT "measured
#: absent": the rubric excuses "weld overlaps of a few mm", and the veto that trusted every
#: WARN was switching off 163 interpenetration claims — and, since 2026-08-30, a stile 17 mm
#: through a seat is a WARN too.  From here up the judge's reading stands and the picture
#: decides.  The line is 8 mm, not 5: the paired re-judge marked the pipe tee's 5.8 mm
#: branch socket — the canonical DESIGNED weld — as the defect the moment a 5 mm line let
#: the claim stand (−0.32 on that side), and the corpus holds only 9 standing claims in the
#: 5–8 mm band (sockets, vise rails) against 16 at 8–10 mm and 43 with an ERROR behind them.
VETO_PENETRATION_DEPTH_M = 0.008


def _rule_gates_clean(rule: CapRule, gates: list[GateReport]) -> bool:
    """Every gate ``rule`` watches ran, passed, and has no finding of its ``kinds`` that is
    an ERROR — or a penetration WARN measured at ``VETO_PENETRATION_DEPTH_M`` or deeper.
    A report is watched when its name OR one of its findings' ``gate`` matches the rule,
    the same predicate the cap side (``_finding_matches``) uses."""
    watched = [g for g in gates if fnmatch(g.gate, rule.gate) or any(fnmatch(f.gate, rule.gate) for f in g.findings)]
    if not watched:
        return False
    for g in watched:
        if not g.passed:
            return False
        for f in g.findings:
            if not any(k.lower() in _finding_tokens(f) for k in rule.kinds):
                continue
            if f.severity == Severity.ERROR:
                return False
            depth = f.data.get("depth_m") if isinstance(f.data, dict) else None
            if f.severity == Severity.WARN and isinstance(depth, (int, float)) and depth >= VETO_PENETRATION_DEPTH_M:
                return False
    return True


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

    Only the first matching finding per rule is recorded as evidence (one ledger
    line per rule), but all rules are evaluated.  ``defects_present`` (checklist
    id → True) adds one ``defect:<id>`` ledger line per present defect that
    carries a ``cap``.
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
        # A flat cap was the DECISIVE cap on 121 of 424 static_object verdicts (28.6 %,
        # audited 2026-08-30): one unverified must item out of ten pinned the score to
        # 0.600 exactly as ten out of ten did.  Graded, the cap keeps its floor at ``cap``
        # (0 of n verified) and climbs linearly to 1.0; pass/fail is untouched — the
        # verdict still fails on ``must_missing``.
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


# ===================================================================== scoring
SCORING_VERSION = 2
"""The arithmetic downstream of the model, stamped into every ``ScoreBreakdown``.

A stored verdict is reproducible from its samples only under the version that wrote
it (``eval/bench/rejudge_offline.py --identity`` compares nothing else); an older stamp is
drift to report, not a fault.

0 — before 2026-08-26 (no stamp on disk): majority vote, penalties, the cap ladder.
1 — 2026-08-26, 7a9b6d3: ``veto_measured_defects`` — a checklist defect a passed gate
    measured absent neither penalises nor caps.  (Unstamped; 41 corpus verdicts predate
    it and move on replay.)
2 — 2026-08-30: ``CapRule.measures`` (the veto reaches ``interpenetration``), the object
    rubrics' floating/penetration rules watch ``connectivity`` alone, a defect vote tie
    is absent, ``missing_must_acceptance`` is graded, ``overridden`` + this stamp in raw.
"""


class ScoreBreakdown(BaseModel):
    """Everything the pass/fail decision used (serialised into ``Judgment.raw``)."""

    status: str = Field(default="ok", description="ok | degraded")
    scoring_version: int = Field(default=0, description="SCORING_VERSION that produced this breakdown")
    per_criterion_mean: dict[str, float]
    per_criterion_std: dict[str, float]
    per_sample_overall: list[float]
    overall_uncapped: float = Field(
        description="rubric-weighted mean of the per-criterion means, before defects/caps"
    )
    overall_after_defects: float = Field(
        default=0.0, description="overall_uncapped minus defect penalties"
    )
    overall: float
    floors_hit: list[dict[str, Any]] = Field(default_factory=list)
    caps: CapResult
    must_missing: list[str] = Field(default_factory=list)
    acceptance_votes: dict[str, list[bool]] = Field(default_factory=dict)
    defects: dict[str, bool] = Field(
        default_factory=dict, description="checklist id → present (majority vote)"
    )
    defect_votes: dict[str, list[bool]] = Field(default_factory=dict)
    overridden: list[str] = Field(default_factory=list,
                                  description="checklist defects the vote marked present that a passed gate measured absent (veto_measured_defects)")
    defect_penalty: float = 0.0
    n_requested: int
    n_used: int
    sample_errors: list[str] = Field(default_factory=list)
    tie_broken: list[str] = Field(default_factory=list,
                                  description="ids an exact vote tie decided: acceptance → the representative sample, defect → absent")
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
    """Majority of ``votes``; an exact tie takes ``tie_break``.  Returns (decision, was_tie).

    Acceptance ties take the representative sample (the owner's policy: a must-item tie
    decides pass/fail, and the old ties→False failed a run on one dissent).  Defect ties
    take ``False``: the rubric puts the burden of proof on the defect ("present only when an
    image or a gate finding shows it"), so a split vote has not met it.  419 of 420 corpus
    verdicts are n=1 — this moves nothing on disk (2026-08-30).
    """
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
        {"criterion": cid, "score": round(sc, 4), "floor": fl}
        for cid, sc, fl in rubric.floors_hit(mean_scores)
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
        defects[did], tied = _vote(v, False)
        if tied:
            tie_broken.append(did)
    # a checklist defect that a passed gate measured as absent neither penalises nor caps
    # (``veto_measured_defects``); it is still named in the verdict tail
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
        overridden=overridden,
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


def degraded_judgment(
    rubric: Rubric, error: str, *, usage: Usage, judge_backend: str, n_requested: int, judge_prompt_hash: str = ""
) -> Judgment:
    """A verdict the orchestrator must treat as a GLITCH (not a score): ``raw.status == 'degraded'``."""
    breakdown = {
        "status": "degraded",
        "scoring_version": SCORING_VERSION,
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
