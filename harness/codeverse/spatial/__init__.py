"""Language-agnostic spatial tools (on GLB / URDF / scene bundles).

Tools are registered with ``@tool`` (see ``registry.py``) so ONE definition
yields: a python callable, a JSON schema for native tool-calling, an MCP tool,
and a prompt card.  Observations are structured (text + numbers + images).
"""

from codeverse.spatial.registry import (
    Observation,
    ToolDef,
    ToolUsageError,
    get_tool,
    list_tools,
    tool,
)

__all__ = ["Observation", "ToolDef", "ToolUsageError", "get_tool", "list_tools", "tool"]
