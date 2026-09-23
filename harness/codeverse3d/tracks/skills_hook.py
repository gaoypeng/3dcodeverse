"""Where the skill system meets a round: route before generating, measure after.

Kept out of ``codeverse3d/skills`` so that package stays pure (no ``RunContext``, no
workspace conventions, no environment) and testable without a run.  Kept out of
``steps.py`` so a round's control flow does not grow a second subject.

Everything here is a no-op when ``C3D_SKILLS=0`` (the system is ON by default since
2026-09-22), and every function swallows its own failures: a skill that cannot be routed,
written or probed must cost that skill, not the round.  The switch is ``Settings.skills``,
read at the call.  ``record_usage`` reads what the round's sessions did
from their CLIs' own tool calls (``skills/telemetry.probe_reads``), atime only as fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from codeverse3d.contracts.artifacts import GateReport
from codeverse3d.contracts.run import SkillsUsage
from codeverse3d.tracks.common import RunContext

log = logging.getLogger(__name__)

CTX_KEY = "skills"  # ctx.extra slot holding this round's SkillsMaterialized


def attach_for_round(ctx: RunContext, *, index: int, kind: str, findings: Sequence[GateReport] = ()) -> Any | None:
    """Route + materialise this round's skills.  Returns the ``SkillsMaterialized``.

    ``findings``: the previous round's gates — the input no CLI's own loader can see."""
    from codeverse3d.config import get_settings
    from codeverse3d.skills import attach_skills

    # drop the previous round's set first: a failure below must not leave this round
    # probing — and crediting — bundles it never attached
    ctx.extra.pop(CTX_KEY, None)
    if not get_settings().skills:
        return None
    try:
        got = attach_skills(
            ctx.ws.root,
            track=ctx.spec.track.value if hasattr(ctx.spec.track, "value") else str(ctx.spec.track),
            language=ctx.language.value,
            kind=kind,
            plan=ctx.plan,
            findings=list(findings),
            single_shot=ctx.single_shot,
        )
    except Exception as e:  # noqa: BLE001 — never let the skill system break a round
        log.warning("skills not attached for round %d (%s): %s", index, kind, e)
        return None
    ctx.extra[CTX_KEY] = got
    # the body a session actually saw is part of what decided the run: hash it beside the
    # prompt hashes so a later comparison of two runs can tell "same skill, new wording"
    # from "different skill"
    for sel in got.selections:
        ctx.record_prompt(f"skill:{sel.name}", sel.skill.body)
    ctx.events.emit("skills.attached", round=index, kind=kind, skills=got.listed,
                    index_tokens=got.index_tokens, inlined=got.inlined or None, reasons=got.reasons)
    return got


def with_inlined_skill(ctx: RunContext, tasks: Sequence[Any]) -> list[Any]:
    """Single-shot only: prepend the one routed body to each task prompt.

    A single-shot call has no read loop, so a pointer to a file it cannot open would be
    cost with no benefit — and, worse, would make the single-shot arm quietly
    incomparable with the agent arms.  One body, capped, in the prompt.
    """
    got = ctx.extra.get(CTX_KEY)
    if not got or not getattr(got, "inlined", ""):
        return list(tasks)
    from codeverse3d.skills.prompting import inline_body

    _, text = inline_body(got.selections)
    if not text:
        return list(tasks)
    return [t.model_copy(update={"prompt": f"{text}\n---\n\n{t.prompt}"}) for t in tasks]


def repair_pointers(ctx: RunContext, lint: GateReport | None) -> str:
    """Name the attached skills that answer the CURRENT lint/build failure.

    ``tracks/repair.py`` already picks a cookbook section by keyword; this is the same
    idea one level up, and it only names skills that are already in the workspace, so it
    adds pointers rather than text.
    """
    got = ctx.extra.get(CTX_KEY)
    if not got or not getattr(got, "selections", None):
        return ""
    from codeverse3d.skills.prompting import repair_pointers as _fmt
    from codeverse3d.skills.registry import ROUTES, finding_kinds

    kinds = finding_kinds([lint]) if lint is not None else []
    # ANY route for the skill, not just the rules that fired at attach time: the build/lint failure a
    # repair session is looking at was discovered afterwards, and the sheet that answers it is
    # already sitting in the workspace
    answering = [s.name for s in got.selections if any(r.skill == s.name and r.matches_finding(kinds) for r in ROUTES)]
    return _fmt(got.selections, agent_kind=ctx.agent_kind, answering=answering)


def record_usage(ctx: RunContext, *, index: int, kind: str) -> SkillsUsage | None:
    """After the round's sessions: what was read.  Also appends ``telemetry/skills.jsonl``."""
    got = ctx.extra.get(CTX_KEY)
    if not got:
        return None
    try:
        from codeverse3d.skills.telemetry import append_usage, probe_reads

        usage = probe_reads(ctx.ws.root, got)
        append_usage(ctx.ws.root, usage, round=index, kind=kind, agent=ctx.agent_id,
                     track=ctx.spec.track.value if hasattr(ctx.spec.track, "value") else str(ctx.spec.track),
                     language=ctx.language.value)
    except Exception as e:  # noqa: BLE001 — telemetry never costs a round
        log.warning("skills telemetry failed for round %d: %s", index, e)
        return None
    ctx.events.emit("skills.read", round=index, listed=usage.listed, deep=usage.deep,
                    surfaced=usage.surfaced, body_tokens_read=usage.body_tokens_read)
    return usage


__all__ = ["CTX_KEY", "attach_for_round", "record_usage", "repair_pointers", "with_inlined_skill"]
