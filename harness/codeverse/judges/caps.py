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
    return [
        a.id
        for a in acceptance_items
        if a.priority == "must" and not acceptance_results.get(a.id, False)
    ]


def measured_absent(rubric: Rubric, defect_id: str, gates: list[GateReport]) -> bool:
    """True when a deterministic gate MEASURED the thing this checklist defect claims, and found nothing.

    A checklist defect is a VLM's reading of a picture.  Some of them — a floating part, an
    interpenetration — are also what a gate measures on the exported mesh, and the rubric
    already says which: a ``when="gate"`` cap rule with the same id and a ``kinds`` list.
    When every gate that rule watches ran and passed with no ERROR finding of those kinds,
    the measurement contradicts the perception, and the perception must not cap.

    Measured 2026-08-26 on a plan-pinned pair (fancy_v1 gas_street_lamp): the connectivity
    gate said "all 9 parts connected, gap <= 2 mm"; the judge read the dark seam under the
    pedestal as "floating in mid-air, a clear daylight gap", marked floating_part present, and
    ``defect:floating_part`` capped the run at 0.6 (uncapped 0.72) against 0.96 for a sibling
    the eye cannot tell apart.  CLAUDE.md law 3: gates decide geometry, the VLM is perception.

    A defect with no matching gate rule (wrong_object, missing_named_part …) is never
    overridden: nothing measured it.  A gate that did not run overrides nothing either.
    """
    rule = next(
        (r for r in rubric.caps if r.when == "gate" and r.id == defect_id and r.kinds), None
    )
    if rule is None:
        return False
    watched = [g for g in gates if fnmatch(g.gate, rule.gate)]
    if not watched:
        return False
    for g in watched:
        if not g.passed:
            return False
        for f in g.findings:
            if f.severity == Severity.ERROR and any(
                k in (f.message or "").lower() for k in rule.kinds
            ):
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
        evidence = _rule_evidence(
            rule,
            gates,
            acceptance_results,
            acceptance_items or [],
            console_errors or [],
            views or [],
        )
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
