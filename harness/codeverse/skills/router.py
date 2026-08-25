"""Derive the skill set from typed inputs — nobody writes ``skills: [...]`` by hand.

The reference library put a ``skills: list[str]`` field on each task and a human filled
it; that scales to one author and drifts the moment the failure modes change.  Here the
set falls out of five inputs the harness already has (track, language, round kind, plan
signals, and the PREVIOUS round's gate findings), through the typed table in
``registry.py``.  The last input is the one no CLI's own skill loader can see, and it is
why a repair round gets the sheet for what actually broke.

Everything in this module is pure: no environment, no filesystem beyond the library the
caller passes in.  The switches are applied by the caller (``materialize.attach_skills``)
so a test can route without touching ``os.environ``.
"""

from __future__ import annotations

import logging
from typing import Any

from codeverse.skills.model import EVIDENCE_INHERITED, Selection, Skill
from codeverse.skills.registry import QUIET_KINDS, ROUTES, Route, finding_kinds

log = logging.getLogger(__name__)

#: plan-derived booleans/ints the table may test.  Kept here (not on the Plan contracts)
#: because they are a routing concern: adding one must not migrate every stored plan.
SIGNAL_KEYS = ("n_parts", "multi_part", "has_instances", "has_symmetry", "has_assemblies",
               "has_joints", "joint_types", "has_custom_shader")

_SHADER_WORDS = ("shader", "glsl", "onbeforecompile", "shadermaterial", "custom material",
                 "raymarch", "postprocess", "post-process")


def plan_signals(plan: Any | None) -> dict[str, Any]:
    """Route-relevant facts about a plan.  Tolerant by design: a missing/partial plan
    yields all-false signals rather than raising, because a planner failure must not
    also take out the round's skills."""
    parts = list(getattr(plan, "parts", None) or [])
    joints = list(getattr(plan, "joints", None) or [])
    effects = list(getattr(plan, "effects", None) or [])
    passes = list(getattr(plan, "passes", None) or [])
    text = " ".join(str(x) for x in (
        getattr(plan, "summary", ""), getattr(plan, "style_notes", ""), getattr(plan, "environment", ""),
        getattr(plan, "style", ""), getattr(plan, "motion", ""),
        *(f"{getattr(e, 'kind', '')} {getattr(e, 'description', '')}" for e in effects),
        *(f"{getattr(p, 'kind', '')} {getattr(p, 'description', '')}" for p in passes),
    )).lower()
    n_parts = len(parts)
    return {
        "n_parts": n_parts,
        "multi_part": n_parts >= 2,
        "has_instances": any(int(getattr(p, "instances", 1) or 1) > 1 for p in parts),
        "has_symmetry": any(str(getattr(p, "symmetry", "none") or "none") != "none" for p in parts),
        "has_assemblies": any(getattr(p, "children", None) for p in parts),
        "has_joints": bool(joints),
        "joint_types": sorted({str(getattr(j, "type", "")) for j in joints if getattr(j, "type", "")}),
        "has_custom_shader": bool(effects) or any(w in text for w in _SHADER_WORDS),
    }


def _row_applies(row: Route, track: str, language: str, kind: str, signals: dict[str, Any]) -> bool:
    if row.tracks and track not in row.tracks:
        return False
    if row.languages and language not in row.languages:
        return False
    if row.kinds and kind not in row.kinds:
        return False
    if any(not signals.get(k) for k in row.requires_all):
        return False
    return not (row.requires_any and not any(signals.get(k) for k in row.requires_any))


def select(
    track: str,
    language: str,
    kind: str,
    *,
    signals: dict[str, Any] | None = None,
    findings: Any = (),
    library: dict[str, Skill] | None = None,
    max_skills: int = 5,
    allow_unverified: bool = False,
) -> list[Selection]:
    """The routed bundles for one session, ranked, capped, each with its reason.

    ``findings`` may be ``GateFinding``s, ``GateReport``s, or already-classified kind
    strings — the caller usually has the previous ``RoundRecord.gates`` on hand.
    """
    from codeverse.skills import all_skills

    lib = library if library is not None else all_skills()
    sig = dict(signals or {})
    kinds = [f for f in findings if isinstance(f, str)] if findings else []
    if findings and not kinds:
        kinds = finding_kinds(findings)

    best: dict[str, Selection] = {}
    first_seen: dict[str, int] = {}
    for order, row in enumerate(ROUTES):
        hit = row.matches_finding(kinds) if row.gate_fired else None
        if row.gate_fired and hit is None:
            continue
        if not row.gate_fired and kind in QUIET_KINDS:
            # design §5.2 law 4: short, narrow sessions attach nothing on their own
            continue
        if not _row_applies(row, track, language, kind, sig):
            continue
        skill = lib.get(row.skill)
        if skill is None:
            # The Author phase ships bodies in its own commits; a table row without a
            # bundle must not crash a run — it is a missing skill, not a broken harness.
            log.debug("route %s names skill %r, which is not in the library", row.rule, row.skill)
            continue
        if skill.evidence == EVIDENCE_INHERITED and not allow_unverified:
            log.debug("skill %s is %s and CV3D_SKILLS_UNVERIFIED is off; not routed", skill.name, EVIDENCE_INHERITED)
            continue
        reason = f"{row.rule}: {row.why}" + (f" [{hit}]" if hit else "")
        first_seen.setdefault(row.skill, order)
        prev = best.get(row.skill)
        if prev is None:
            best[row.skill] = Selection(skill=skill, priority=row.priority, rules=(row.rule,), reason=reason)
        elif row.priority > prev.priority:
            # a gate-fired row overrides the standing one, and says so in the reason
            best[row.skill] = Selection(skill=skill, priority=row.priority, rules=(*prev.rules, row.rule), reason=reason)
        else:
            best[row.skill] = prev.model_copy(update={"rules": (*prev.rules, row.rule)})

    # priority first (law 2: gate-fired rows outrank standing ones), then table order —
    # a stable, testable ranking, so the cap always cuts the same tail.
    ranked = sorted(best.values(), key=lambda s: (-s.priority, first_seen[s.name]))
    return ranked[:max(0, max_skills)]


def skills_for(
    track: str,
    language: str,
    kind: str,
    *,
    plan: Any | None = None,
    findings: Any = (),
    library: dict[str, Skill] | None = None,
    max_skills: int = 5,
    allow_unverified: bool = False,
) -> list[Skill]:
    """``select`` without the reasons — the shape most callers want."""
    sel = select(track, language, kind, signals=plan_signals(plan), findings=findings,
                 library=library, max_skills=max_skills, allow_unverified=allow_unverified)
    return [s.skill for s in sel]


__all__ = ["SIGNAL_KEYS", "plan_signals", "select", "skills_for"]
