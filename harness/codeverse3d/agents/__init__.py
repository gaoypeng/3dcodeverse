"""CodingAgent backends (agentic sessions on a workspace).

Ids: ``gemini-cli:<model>`` · ``claude-code:<model>`` · ``codex:<model>`` ·
``agy:<model>``.
"""

from codeverse3d.agents.registry import get_coding_agent

__all__ = ["get_coding_agent"]
