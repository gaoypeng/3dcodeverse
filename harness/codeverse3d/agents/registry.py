"""The CodingAgent protocol every agentic backend implements."""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from codeverse3d.contracts.agent import AgentJob, AgentResult


class CodingAgent(Protocol):
    """Runs ONE headless agent session in ``job.workspace``.

    Implementations never raise for agent failures (return ``AgentResult(ok=False, ...)``),
    write only under ``job.write_roots``, and compute ``files_changed`` from git.
    """

    kind: str  # claude-code | codex | agy
    model: str

    def run(self, job: AgentJob) -> AgentResult: ...

    def available(self) -> tuple[bool, str]:
        """(is_usable, reason) — binary found / auth present / model known."""
        ...


# ===================================================================== registry
KINDS = ("claude-code", "codex", "agy")


def parse_agent_id(agent_id: str) -> tuple[str, str]:
    """``agy:gemini-4-argon`` → ('agy', 'gemini-4-argon')."""
    if ":" not in agent_id:
        raise ValueError(f"agent id must be '<kind>:<model>', got {agent_id!r}")
    kind, model = agent_id.split(":", 1)
    if kind not in KINDS:
        raise ValueError(f"unknown agent kind {kind!r}; known: {KINDS}")
    return kind, model


def get_coding_agent(agent_id: str) -> CodingAgent:
    """The (metered) CodingAgent for ``<kind>:<model>``."""
    from codeverse3d.cost.instrument import metered_agent

    return metered_agent(_build_agent(agent_id))


@lru_cache(maxsize=32)
def _build_agent(agent_id: str) -> CodingAgent:
    kind, model = parse_agent_id(agent_id)  # an unknown kind has raised already
    from codeverse3d.agents import backends

    return {"claude-code": backends.ClaudeCodeAgent, "codex": backends.CodexAgent,
            "agy": backends.AntigravityAgent}[kind](model)
