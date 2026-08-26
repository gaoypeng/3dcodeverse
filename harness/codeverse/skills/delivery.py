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

from dataclasses import dataclass

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

    @property
    def needs_tool(self) -> bool:
        """``read_skill``: the routed set as an affordance rather than as prose."""
        return not self.native_loader


#: The whole per-backend policy.  One row per backend; anything absent is loaderless.
_BACKENDS: dict[str, Delivery] = {
    "claude-code": Delivery(root=CLAUDE_SKILL_ROOT, native_loader=True),
    "codex": Delivery(root=AGENTS_SKILL_ROOT, native_loader=True),
    "gemini-cli": Delivery(root=AGENTS_SKILL_ROOT, native_loader=True),
    "agy": Delivery(root=AGENTS_SKILL_ROOT, native_loader=True),
    "api-agent": Delivery(root=AGENTS_SKILL_ROOT, native_loader=False),
}

#: the safe answer for a backend nobody has classified yet
_UNKNOWN = Delivery(root=AGENTS_SKILL_ROOT, native_loader=False)


def delivery_for(agent_kind: str) -> Delivery:
    """The delivery policy for ``agent_kind``; unknown kinds get the loaderless answer."""
    return _BACKENDS.get((agent_kind or "").split(":", 1)[0], _UNKNOWN)


def known_backends() -> tuple[str, ...]:
    return tuple(_BACKENDS)
