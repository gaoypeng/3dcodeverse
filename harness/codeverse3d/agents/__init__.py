"""CodingAgent backends (agentic sessions on a workspace).

Ids: ``agy:<model>`` (Google's models) · ``claude-code:<model>`` · ``codex:<model>``.
"""

from codeverse3d.agents.registry import get_coding_agent

__all__ = ["get_coding_agent"]
