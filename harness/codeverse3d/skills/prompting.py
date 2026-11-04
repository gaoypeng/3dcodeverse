"""How the shared skill library reaches each coding agent: the discovery root each backend reads.

Bundles are plain ``SKILL.md`` files; all three CLIs have a native skill loader, so only the
mandate is added to the instructions.
"""

from __future__ import annotations

from collections.abc import Sequence

from codeverse3d.cost.guard import text_tokens
from codeverse3d.skills.model import Selection, Skill

__all__ = ["AGENTS_SKILL_ROOT", "CLAUDE_SKILL_ROOT", "skill_path"]

#: discovery roots, relative to the workspace root
AGENTS_SKILL_ROOT = ".agents/skills"
CLAUDE_SKILL_ROOT = ".claude/skills"

#: per-backend discovery root; an unlisted kind reads the shared ``.agents`` root.
_BACKEND_ROOTS: dict[str, str] = {
    "claude-code": CLAUDE_SKILL_ROOT,
    "codex": AGENTS_SKILL_ROOT,
    "agy": AGENTS_SKILL_ROOT,
}


# ===================================================================== prompting
#: what every backend is told.
MANDATE = ("Skills for this task are installed in this workspace. Any whose description matches "
           "your task are MANDATORY — activate and read them before writing code.")

_HEADING = "## Skills"


def skill_path(name: str, *, agent_kind: str = "") -> str:
    """Where ``agent_kind``'s loader finds bundle ``name`` (``codex:gpt-6.1-sol`` is codex)."""
    root = _BACKEND_ROOTS.get((agent_kind or "").split(":", 1)[0], AGENTS_SKILL_ROOT)
    return f"{root}/{name}/SKILL.md"


def index_block(skills: Sequence[Skill | Selection]) -> str:
    """The mandate appended to the AGENTS.md body ('' when nothing is routed)."""
    return f"{_HEADING}\n\n{MANDATE}\n" if skills else ""


def index_tokens(skills: Sequence[Skill | Selection]) -> int:
    """Token cost of the index in message 0."""
    return text_tokens(index_block(skills))


def repair_pointers(selections: Sequence[Selection], *, agent_kind: str = "",
                    answering: Sequence[str] = ()) -> str:
    """Name the skills that answer THIS failure (repair rounds only): those a previous gate
    routed, plus the names in ``answering``.
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
    """``(name, text)`` for the single-shot arm: the highest-priority body that fits, inlined."""
    for s in selections:
        if s.skill.body_tokens <= max_tokens:
            return s.name, f"{_HEADING} — {s.name}\n\n{s.skill.description}\n\n{s.skill.body.strip()}\n"
    return "", ""
