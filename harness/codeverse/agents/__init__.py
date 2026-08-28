"""CodingAgent backends (agentic sessions on a workspace).

Ids: ``gemini-cli:<model>`` · ``claude-code:<model>`` · ``codex:<model>`` ·
``agy:<model>``.
"""

from codeverse.agents.base import CodingAgent
from codeverse.agents.registry import get_coding_agent, parse_agent_id

__all__ = ["CodingAgent", "get_coding_agent", "parse_agent_id"]
