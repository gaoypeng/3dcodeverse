"""How one shared skill library reaches each coding agent — the ONE place that differs.

The library itself is agent-agnostic: plain `agentskills.io` bundles under
``codeverse/skills/<name>/SKILL.md``, routed by ``router.py`` from typed inputs.  Nothing
in it knows what a backend is.  What genuinely differs between backends is only
**delivery**, and only in two bits:

* does the backend have a NATIVE skill loader (it finds and indexes bundles itself), and
* which discovery root does it read?

Both were read out of the shipped binaries, not assumed: gemini-cli, codex and agy read
``<ws>/.agents/skills/<name>/SKILL.md``; claude-code 2.1 reads ``<ws>/.claude/skills/…``
and has no ``.agents`` skill root at all.

Everything else follows from those two bits, so adding a backend is ONE row here — not a
new module, not a branch in three files.  A backend nobody has classified gets the safe
answer (no native loader), which means it is handed the explicit index *and* the
``read_skill`` tool: over-delivering costs tokens, under-delivering costs the skill.

WHY the tool exists at all, measured 2026-08-25: on the same library and workspace, the
three native loaders read **5 of 5** routed bundles unprompted while api-agent read
**0 of 5** — not from unwillingness (it made 52 ``read_file`` calls that session) but
because the bundles sit in hidden directories its ``list_files`` skips, and the index was
prose in a 2.3 kB system prompt rather than an affordance.  So a backend without a loader
gets the routed set as a TOOL.  That is a general rule about loaderless backends, not a
special case for one of them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from codeverse.cost.guard import text_tokens
from codeverse.skills.model import Selection, Skill

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
    native_loader: bool
    """True when the backend indexes bundles itself.  Such a backend must not be handed a
    second index — it would list the same skills twice — and needs no tool."""

    @property
    def needs_index(self) -> bool:
        """An explicit one-line-per-skill index in the agent body file."""
        return not self.native_loader


#: The whole per-backend policy.  One row per backend; anything absent is loaderless.
_BACKENDS: dict[str, Delivery] = {
    "claude-code": Delivery(root=CLAUDE_SKILL_ROOT, native_loader=True),
    "codex": Delivery(root=AGENTS_SKILL_ROOT, native_loader=True),
    "gemini-cli": Delivery(root=AGENTS_SKILL_ROOT, native_loader=True),
    "agy": Delivery(root=AGENTS_SKILL_ROOT, native_loader=True),
}

#: the safe answer for a backend nobody has classified yet
_UNKNOWN = Delivery(root=AGENTS_SKILL_ROOT, native_loader=False)


def delivery_for(agent_kind: str) -> Delivery:
    """The delivery policy for ``agent_kind``; unknown kinds get the loaderless answer."""
    return _BACKENDS.get((agent_kind or "").split(":", 1)[0], _UNKNOWN)


def known_backends() -> tuple[str, ...]:
    return tuple(_BACKENDS)


# ===================================================================== prompting
#: Per-backend policy lives in ONE place (``skills/delivery.py``); these are re-exported so
#: existing importers keep working and so nothing here re-derives what a backend needs.
NATIVE_LOADERS = tuple(k for k in known_backends() if delivery_for(k).native_loader)

#: what a NATIVE loader is told.  Its own index lists every skill it can see, including any
#: the user installed globally, so "the ones that match" is the right instruction there.
MANDATE = ("Skills for this task are installed in this workspace. Any whose description matches "
           "your task are MANDATORY — activate and read them before writing code.")
#: what an unclassified (loaderless) backend is told.  The list under it was chosen by OUR
#: router from the track, the language, the plan and the previous round's gate findings —
#: inviting the agent to filter it again would only lose reads.
MANDATE_ROUTED = ("The skills below were selected for THIS task by the harness, from your track, "
                  "your language, your plan and the gate findings of the previous round. Reading "
                  "them is MANDATORY — open each one before writing code.")

_HEADING = "## Skills"

#: how much of a description the routed index repeats.  Descriptions run to 1024 chars for
#: the CLIs' own matchers; our index is not a matcher — the router already decided — so a
#: line only has to be recognisable enough to know which file to open.  Measured on the
#: real library: five full descriptions cost 780 tokens; the first clause costs 401.
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
    return f"{delivery_for(agent_kind).root}/{name}/SKILL.md"


def index_block(skills: Sequence[Skill | Selection], agent_kind: str) -> str:
    """The text appended to the AGENTS.md body for ``agent_kind`` ('' when nothing is added).

    Native-loader backends get the mandate only; an unclassified backend (the safe
    assumption for anything unknown) also gets the one-line-per-skill index it cannot
    discover itself.
    """
    items = [s.skill if isinstance(s, Selection) else s for s in skills]
    if not items:
        return ""
    if not delivery_for(agent_kind).needs_index:
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
