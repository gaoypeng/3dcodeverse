"""The CodingAgent protocol every agentic backend implements."""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

from codeverse3d.contracts.agent import AgentJob, AgentResult


@runtime_checkable
class CodingAgent(Protocol):
    """Runs ONE headless agent session in ``job.workspace``.

    Contract for implementations:
    * the agent may only write under ``job.write_roots`` (harness materialises
      AGENTS.md/GEMINI.md/CLAUDE.md + MCP config before the call; see
      ``agents/materialize.py``),
    * never raise for agent failures — return ``AgentResult(ok=False, exit_reason=...)``,
    * fill ``usage`` (tokens + cost) as precisely as the CLI allows,
    * write the full transcript to ``job.workspace/trajectories/...`` and set
      ``transcript_path``,
    * compute ``files_changed`` via ``Workspace.changed_files`` (git), not by
      trusting the agent's claims,
    * respect ``job.timeout_s`` with an activity-aware watchdog (kill the whole
      process group on timeout).
    """

    kind: str  # gemini-cli | claude-code | codex | agy
    model: str

    @property
    def id(self) -> str: ...

    def run(self, job: AgentJob) -> AgentResult: ...

    def available(self) -> tuple[bool, str]:
        """(is_usable, reason) — binary found / auth present / model known."""
        ...


# ===================================================================== registry
KINDS = ("gemini-cli", "claude-code", "codex", "agy")


def parse_agent_id(agent_id: str) -> tuple[str, str]:
    """``gemini-cli:gemini-3.6-flash`` → ('gemini-cli', 'gemini-3.6-flash')."""
    if ":" not in agent_id:
        raise ValueError(f"agent id must be '<kind>:<model>', got {agent_id!r}")
    kind, model = agent_id.split(":", 1)
    if kind not in KINDS:
        raise ValueError(f"unknown agent kind {kind!r}; known: {KINDS}")
    return kind, model


def get_coding_agent(agent_id: str) -> CodingAgent:
    """The (metered) CodingAgent for ``<kind>:<model>`` — the same wrap
    ``models.registry.get_chat_model`` gives a chat model."""
    from codeverse3d.cost.instrument import metered_agent

    return metered_agent(_build_agent(agent_id))


@lru_cache(maxsize=32)
def _build_agent(agent_id: str) -> CodingAgent:
    kind, model = parse_agent_id(agent_id)
    if kind == "gemini-cli":
        from codeverse3d.agents.backends import GeminiCliAgent

        return GeminiCliAgent(model)
    if kind == "claude-code":
        from codeverse3d.agents.backends import ClaudeCodeAgent

        return ClaudeCodeAgent(model)
    if kind == "codex":
        from codeverse3d.agents.backends import CodexAgent

        return CodexAgent(model)
    if kind == "agy":
        from codeverse3d.agents.backends import AntigravityAgent

        return AntigravityAgent(model)
    raise ValueError(f"unknown agent kind {kind!r}; known: {KINDS}")
