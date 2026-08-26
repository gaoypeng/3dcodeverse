"""Where the skill system meets a round: route before generating, measure after.

Kept out of ``codeverse/skills`` so that package stays pure (no ``RunContext``, no
workspace conventions, no environment) and testable without a run.  Kept out of
``steps.py`` so a round's control flow does not grow a second subject.

Everything here is a no-op unless ``CV3D_SKILLS`` is on, and every function swallows its
own failures: a skill that cannot be routed, written or probed must cost that skill, not
the round.  The switch is read at CALL time (see ``skills/config.py`` for why).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeverse.contracts.artifacts import GateReport
from codeverse.contracts.skills import SkillsUsage
from codeverse.tracks.common import RunContext

log = logging.getLogger(__name__)

CTX_KEY = "skills"  # ctx.extra slot holding this round's SkillsMaterialized


def _previous_findings(ctx: RunContext, index: int) -> list[GateReport]:
    """The gates of the last completed round — the input no CLI's own loader can see.

    Read straight off disk rather than through ``steps.load_round_records`` so the hook
    stays independent of the round loop (and so a half-written record from a killed run
    costs this round its gate routing, not the round itself)."""
    if index <= 0:
        return []
    d = Path(ctx.ws.root) / "rounds"
    if not d.is_dir():
        return []
    for p in sorted(d.glob("r*.json"), reverse=True):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.debug("skills: unreadable round record %s: %s", p, e)
            continue
        if int(data.get("index", -1)) >= index:
            continue
        gates = data.get("gates") or []
        if gates:
            try:
                return [GateReport.model_validate(g) for g in gates]
            except ValidationError as e:
                log.debug("skills: round record %s has gates we cannot parse: %s", p, e)
            return []
    return []


def attach_for_round(ctx: RunContext, *, index: int, kind: str) -> Any | None:
    """Route + materialise this round's skills.  Returns the ``SkillsMaterialized``."""
    from codeverse.skills import attach_skills, skills_enabled

    # drop the previous round's set first: a failure below must not leave this round
    # probing — and crediting — bundles it never attached
    ctx.extra.pop(CTX_KEY, None)
    if not skills_enabled():
        return None
    try:
        got = attach_skills(
            ctx.ws.root,
            track=ctx.spec.track.value if hasattr(ctx.spec.track, "value") else str(ctx.spec.track),
            language=ctx.language.value,
            kind=kind,
            agent_kind=ctx.agent_id.split(":", 1)[0],
            plan=ctx.plan,
            findings=_previous_findings(ctx, index),
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
    from codeverse.skills.prompting import inline_body

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
    from codeverse.skills.prompting import repair_pointers as _fmt
    from codeverse.skills.registry import finding_kinds

    kinds = set(finding_kinds([lint])) if lint is not None else set()
    answering = [s.name for s in got.selections if kinds & _matched(s, kinds)]
    return _fmt(got.selections, agent_kind=ctx.agent_id.split(":", 1)[0], answering=answering)


def _matched(selection: Any, kinds: set[str]) -> set[str]:
    """The current finding kinds this attached skill's routes answer (families included)."""
    out: set[str] = set()
    for pat in _kinds_of(selection):
        family = pat[:-1] if pat.endswith("*") else ""
        out |= {k for k in kinds if (family and k.startswith(family)) or k == pat}
    return out


def _kinds_of(selection: Any) -> tuple[str, ...]:
    """Every finding kind ANY route for this skill answers.

    Deliberately not just the rules that fired at attach time: the build/lint failure a
    repair session is looking at was discovered afterwards, and the sheet that answers it
    is already sitting in the workspace."""
    from codeverse.skills.registry import ROUTES

    return tuple(k for r in ROUTES if r.skill == selection.name for k in r.findings)


def record_usage(ctx: RunContext, *, index: int, kind: str) -> SkillsUsage | None:
    """After the round's sessions: what was read.  Also appends ``telemetry/skills.jsonl``."""
    got = ctx.extra.get(CTX_KEY)
    if not got:
        return None
    try:
        from codeverse.skills.telemetry import append_usage, probe_reads

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
