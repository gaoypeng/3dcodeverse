"""Deterministic caps: bound the overall score from gate findings / acceptance.

The VLM never sees or computes these.  ``apply_caps`` returns the capped overall
and a ledger of which rules fired and why, so the verdict is auditable.
"""

from __future__ import annotations

from fnmatch import fnmatch

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateFinding, GateReport, RenderView, Severity
from codeverse.contracts.plan import AcceptanceItem
from codeverse.judges.rubrics import CapRule, Rubric

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
    return [a.id for a in acceptance_items if a.priority == "must" and not acceptance_results.get(a.id, False)]


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
        evidence = _rule_evidence(rule, gates, acceptance_results, acceptance_items or [], console_errors or [], views or [])
        if evidence is not None:
            applied.append(CapApplied(rule=rule.id, cap=rule.cap, evidence=evidence))
    for did, present in (defects_present or {}).items():
        if not present:
            continue
        try:
            item = rubric.defect(did)
        except KeyError:
            continue
        if item.cap is not None:
            applied.append(CapApplied(rule=f"defect:{did}", cap=item.cap, evidence=f"judge checklist: {item.text[:120]}"))
    capped = overall
    for a in applied:
        capped = min(capped, a.cap)
    return CapResult(overall=max(0.0, min(1.0, capped)), caps_applied=applied)


def _rule_evidence(
    rule: CapRule,
    gates: list[GateReport],
    acceptance_results: dict[str, bool],
    acceptance_items: list[AcceptanceItem],
    console_errors: list[str],
    views: list[RenderView],
) -> str | None:
    if rule.when == "missing_views":
        tokens = [k.lower() for k in rule.kinds]
        if any(any(t in f"{v.name} {v.mode}".lower() for t in tokens) for v in views):
            return None
        return f"no render view matching {rule.kinds} among {len(views)} view(s)"
    if rule.when == "acceptance":
        missing = missing_must_items(acceptance_items, acceptance_results)
        if missing:
            return f"must items not verified: {', '.join(missing[:6])}"
        return None
    if rule.when == "console":
        if console_errors:
            return f"{len(console_errors)} console error(s); first: {console_errors[0][:160]}"
        return None
    for report in gates:
        for f in report.findings:
            if _finding_matches(rule, report, f):
                target = f" [{f.target}]" if f.target else ""
                return f"{report.gate}{target}: {f.message[:200]}"
    return None
