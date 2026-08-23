"""The CodingAgent protocol every agentic backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codeverse.contracts.agent import AgentJob, AgentResult


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

    kind: str  # gemini-cli | claude-code | codex | agy | api-agent
    model: str

    @property
    def id(self) -> str: ...

    def run(self, job: AgentJob) -> AgentResult: ...

    def available(self) -> tuple[bool, str]:
        """(is_usable, reason) — binary found / auth present / model known."""
        ...
