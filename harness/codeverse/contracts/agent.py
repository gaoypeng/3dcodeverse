"""CodingAgent job/result — one headless agentic session on a workspace."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.common import Usage


class FileChange(BaseModel):
    path: str
    status: str  # added | modified | deleted
    lines_added: int = 0
    lines_removed: int = 0


class AgentJob(BaseModel):
    workspace: str
    prompt: str
    system_append: str = ""
    model: str = ""
    label: str = ""
    timeout_s: int = 1800
    max_turns: int = 60
    allow_network: bool = False
    spatial_tools: bool = Field(default=True, description="expose the 3dcv MCP spatial tools to the agent")
    write_roots: list[str] = Field(default_factory=lambda: ["src", "public"], description="dirs the agent may edit")
    env: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    ok: bool
    exit_reason: str = ""  # completed | timeout | error | budget | model_substituted
    text: str = ""
    files_changed: list[FileChange] = Field(default_factory=list)
    transcript_path: str = ""
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0
    tool_calls: int = 0
    errors: list[str] = Field(default_factory=list)
