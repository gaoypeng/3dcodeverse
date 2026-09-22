"""How one shared skill library reaches each coding agent — the ONE place that differs.

The library itself is agent-agnostic: plain `agentskills.io` bundles under
``codeverse3d/skills/<name>/SKILL.md``, routed by ``registry.py`` from typed inputs.  Nothing
in it knows what a backend is.  What genuinely differs between backends is only
**delivery**, and only in one bit: which discovery root does it read?

Read out of the shipped binaries, not assumed: gemini-cli, codex and agy read
``<ws>/.agents/skills/<name>/SKILL.md``; claude-code 2.1 reads ``<ws>/.claude/skills/…``
and has no ``.agents`` skill root at all.  All four have a NATIVE skill loader (they find
and index the bundles themselves), so none is handed a second index — it would list the
same skills twice.  Adding a backend is ONE row here — not a new module, not a branch in
three files.  (The loaderless path — an explicit index plus a ``read_skill`` tool — served
the in-process api-agent and went with it: ``agents/registry.KINDS`` accepts only these four.)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codeverse3d.cost.guard import text_tokens
from codeverse3d.skills.model import Selection, Skill

__all__ = ["AGENTS_SKILL_ROOT", "CLAUDE_SKILL_ROOT", "Delivery", "delivery_for", "known_backends"]

#: discovery roots, relative to the workspace root
AGENTS_SKILL_ROOT = ".agents/skills"
CLAUDE_SKILL_ROOT = ".claude/skills"


@dataclass(frozen=True)
class Delivery:
    """What one backend needs in order to see the routed bundles."""

    root: str
    """Discovery root this backend reads (bundles are written to every known root anyway,
    so one copy is never the only copy — this is the path we NAME to it)."""


#: The whole per-backend policy.  One row per backend; a kind not listed here (a test's
#: fake agent) reads the shared ``.agents`` root.
_BACKENDS: dict[str, Delivery] = {
    "claude-code": Delivery(root=CLAUDE_SKILL_ROOT),
    "codex": Delivery(root=AGENTS_SKILL_ROOT),
    "gemini-cli": Delivery(root=AGENTS_SKILL_ROOT),
    "agy": Delivery(root=AGENTS_SKILL_ROOT),
}


def delivery_for(agent_kind: str) -> Delivery:
    """The delivery policy for ``agent_kind`` (``codex:gpt-5.6-sol`` is codex)."""
    return _BACKENDS.get((agent_kind or "").split(":", 1)[0]) or Delivery(root=AGENTS_SKILL_ROOT)


def known_backends() -> tuple[str, ...]:
    return tuple(_BACKENDS)


# ===================================================================== prompting
#: what every backend is told.  Its own index lists every skill it can see, including any
#: the user installed globally, so "the ones that match" is the right instruction there.
MANDATE = ("Skills for this task are installed in this workspace. Any whose description matches "
           "your task are MANDATORY — activate and read them before writing code.")

_HEADING = "## Skills"


def skill_path(name: str, *, agent_kind: str = "") -> str:
    return f"{delivery_for(agent_kind).root}/{name}/SKILL.md"


def index_block(skills: Sequence[Skill | Selection]) -> str:
    """The text appended to the AGENTS.md body ('' when nothing is routed): the mandate
    only — every backend's native loader already indexes the bundles."""
    return f"{_HEADING}\n\n{MANDATE}\n" if skills else ""


def index_tokens(skills: Sequence[Skill | Selection]) -> int:
    """What the index costs in message 0 — recorded per round so §5.4 is auditable."""
    return text_tokens(index_block(skills))


def repair_pointers(selections: Sequence[Selection], *, agent_kind: str = "",
                    answering: Sequence[str] = ()) -> str:
    """Name the skills that answer THIS failure, beside it (repair rounds only).

    Two ways in: a selection that a previous round's gate already routed, and a name in
    ``answering`` — the skills whose routes cover a finding the current build/lint just
    produced, which is discovered after the round attached its set.  Everything else is
    left out: the standing sheets are already in the workspace, and repeating them in the
    volatile tail would pay twice for the same text and say nothing new.
    """
    want = set(answering)
    fired = [s for s in selections if s.gate_fired or s.name in want]
    if not fired:
        return ""
    lines = ["Skills that answer these findings — read before editing:"]
    lines += [f"- {s.name}: {s.reason.split(': ', 1)[-1]} → `{skill_path(s.name, agent_kind=agent_kind)}`"
              for s in fired]
    return "\n".join(lines)


def inline_body(selections: Sequence[Selection], *, max_tokens: int = 2500) -> tuple[str, str]:
    """``(name, text)`` for the single-shot arm: the one highest-priority body, inlined.

    A single-shot call cannot open a file, so a pointer would be a cost with no benefit
    and would quietly make the arm incomparable with the agent arms.  One body, capped.
    """
    for s in selections:
        if s.skill.body_tokens <= max_tokens:
            return s.name, f"{_HEADING} — {s.name}\n\n{s.skill.description}\n\n{s.skill.body.strip()}\n"
    return "", ""
