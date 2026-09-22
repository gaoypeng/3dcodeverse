"""CodingAgent backends (agentic sessions on a workspace).

Ids: ``gemini-cli:<model>`` · ``claude-code:<model>`` · ``codex:<model>`` ·
``agy:<model>``.
"""

from codeverse3d.agents.registry import CodingAgent, get_coding_agent, parse_agent_id

__all__ = ["CodingAgent", "get_coding_agent", "parse_agent_id"]
