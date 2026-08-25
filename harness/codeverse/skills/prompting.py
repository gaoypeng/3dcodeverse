"""The per-backend text — deliberately the smallest thing that can work.

``docs/COST.md`` §13 measured a stable-prefix-first reordering that duplicated the shared
head into four templates: **+2,925 tokens on the average call**, and it still never
reached Gemini's implicit-cache floor, so it was reverted.  The lesson the skill system
inherits: **do not invent a new prompt head.**  Everything below only adds to text that is
already being sent, and each backend gets the least text that reaches it:

* claude-code / codex / gemini-cli / agy discover ``SKILL.md`` themselves.  Writing our
  own index would double-index the same bundles, so they get ONE sentence (~35 tokens).
* api-agent has no loader, so it gets a one-line-per-skill index inside the AGENTS.md
  body, which is already message 0 of the session.
* a single-shot call has no read loop at all: it gets ONE body inlined, so the cost is
  explicit and bounded instead of a pointer nobody can follow.
* on a repair round the gate-fired skills are named beside the findings they answer, in
  the volatile tail — ``tracks/repair.py`` already picks a cookbook section by keyword,
  this is the same idea one level up.

The mandate sentence is 化用'd from the reference block and ~90% shorter: theirs re-asserts
a score claim in every prompt, and a score claim belongs in a measured report.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from codeverse.cost.guard import text_tokens
from codeverse.skills.model import Selection, Skill

#: where a materialised bundle lives, relative to the workspace root (the `.agents` root
#: is the one gemini-cli, codex and agy all read; claude-code reads its own copy).
AGENTS_SKILL_ROOT = ".agents/skills"
CLAUDE_SKILL_ROOT = ".claude/skills"

#: backends whose own loader indexes the bundles; they must NOT be handed a second index
NATIVE_LOADERS = ("claude-code", "codex", "gemini-cli", "agy")

#: what a NATIVE loader is told.  Its own index lists every skill it can see, including any
#: the user installed globally, so "the ones that match" is the right instruction there.
MANDATE = ("Skills for this task are installed in this workspace. Any whose description matches "
           "your task are MANDATORY — activate and read them before writing code.")
#: what api-agent is told.  The list under it was chosen by OUR router from the track, the
#: language, the plan and the previous round's gate findings — inviting the agent to filter
#: it again would only lose reads, and it cannot see what we used to pick them.
MANDATE_ROUTED = ("The skills below were selected for THIS task by the harness, from your track, "
                  "your language, your plan and the gate findings of the previous round. Reading "
                  "them is MANDATORY — open each one before writing code.")

_HEADING = "## Skills"

#: how much of a description the api-agent index repeats.  The spec lets a description run to
#: 1024 chars and ours use it — they are what a CLI's own matcher reads.  api-agent's index is
#: not a matcher: OUR router already decided, so the line only has to be recognisable enough
#: for the agent to know which file to open.  Measured on the real library, quoting the full
#: 14 descriptions costs 780 tokens for five skills; the first clause costs 401.
INDEX_SUMMARY_CHARS = 200

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`])")


def index_summary(description: str, limit: int = INDEX_SUMMARY_CHARS) -> str:
    """The WHAT half of a description, for the one index we write ourselves."""
    first = _SENTENCE_END.split(description.strip(), 1)[0].strip()
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + " ..."


def skill_path(name: str, *, agent_kind: str = "") -> str:
    root = CLAUDE_SKILL_ROOT if agent_kind == "claude-code" else AGENTS_SKILL_ROOT
    return f"{root}/{name}/SKILL.md"


def index_block(skills: Sequence[Skill | Selection], agent_kind: str) -> str:
    """The text appended to the AGENTS.md body for ``agent_kind`` ('' when nothing is added).

    Native-loader backends get the mandate only; api-agent (and anything unknown, which
    is the safe assumption) also gets the one-line-per-skill index it cannot discover.
    """
    items = [s.skill if isinstance(s, Selection) else s for s in skills]
    if not items:
        return ""
    if agent_kind in NATIVE_LOADERS:
        return f"{_HEADING}\n\n{MANDATE}\n"
    lines = [f"- **{s.name}** — {index_summary(s.description)} "
             f"Read `{skill_path(s.name, agent_kind=agent_kind)}` BEFORE writing code."
             for s in items]
    return f"{_HEADING}\n\n{MANDATE_ROUTED}\n\n" + "\n".join(lines) + "\n"


def index_tokens(skills: Sequence[Skill | Selection], agent_kind: str) -> int:
    """What the index costs in message 0 — recorded per round so §5.4 is auditable."""
    return text_tokens(index_block(skills, agent_kind))


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


__all__ = ["AGENTS_SKILL_ROOT", "CLAUDE_SKILL_ROOT", "INDEX_SUMMARY_CHARS", "MANDATE", "MANDATE_ROUTED",
           "NATIVE_LOADERS", "index_block", "index_summary", "index_tokens", "inline_body",
           "repair_pointers", "skill_path"]
