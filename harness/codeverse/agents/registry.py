"""Agent id parsing + factory."""

from __future__ import annotations

from functools import lru_cache

from codeverse.agents.base import CodingAgent

KINDS = ("gemini-cli", "claude-code", "codex", "agy")


def parse_agent_id(agent_id: str) -> tuple[str, str]:
    """``gemini-cli:gemini-3.6-flash`` → ('gemini-cli', 'gemini-3.6-flash')."""
    if ":" not in agent_id:
        raise ValueError(f"agent id must be '<kind>:<model>', got {agent_id!r}")
    kind, model = agent_id.split(":", 1)
    if kind not in KINDS:
        raise ValueError(f"unknown agent kind {kind!r}; known: {KINDS}")
    return kind, model


@lru_cache(maxsize=32)
def get_coding_agent(agent_id: str) -> CodingAgent:
    kind, model = parse_agent_id(agent_id)
    if kind == "gemini-cli":
        from codeverse.agents.gemini_cli import GeminiCliAgent

        return GeminiCliAgent(model)
    if kind == "claude-code":
        from codeverse.agents.claude_code import ClaudeCodeAgent

        return ClaudeCodeAgent(model)
    if kind == "codex":
        from codeverse.agents.codex import CodexAgent

        return CodexAgent(model)
    if kind == "agy":
        from codeverse.agents.antigravity import AntigravityAgent

        return AntigravityAgent(model)
    raise ValueError(f"unknown agent kind {kind!r}; known: {KINDS}")
