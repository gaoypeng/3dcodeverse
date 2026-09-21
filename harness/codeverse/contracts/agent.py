"""CodingAgent job/result — one headless agentic session on a workspace.

Trust model (cooperative, by design): generated code and agent sessions run with local
filesystem access and no network sandbox; the mitigation is that secrets are scrubbed
from their environment (``codeverse.proc.scrub_secrets`` for the generated-code
runtimes, ``agents/cli_common.hardened_env`` for the coding-agent CLIs).
"""

from __future__ import annotations

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
    label: str = ""
    timeout_s: int = 1800
    #: consumed only by claude-code (``--max-turns``); gemini-cli / codex / agy have no
    #: turn flag and run unbounded except by ``timeout_s`` and the run's wall clock
    max_turns: int = 60
    spatial_tools: bool = Field(
        default=True, description="expose the 3dcode MCP spatial tools to the agent"
    )
    write_roots: list[str] = Field(
        default_factory=lambda: ["src", "public"], description="dirs the agent may edit"
    )
    env: dict[str, str] = Field(default_factory=dict)
    # ------------------------------------------------------------- typed job context
    round: int = Field(default=0, description="round index → trajectory dir + ToolContext")
    kind: str = Field(
        default="", description="task kind: baseline | refine | repair | asset | zone ..."
    )
    language: str = Field(default="", description="spec language (spatial tool filtering)")
    track: str = Field(default="", description="spec track (spatial tool filtering)")
    files_hint: list[str] = Field(
        default_factory=list,
        description="workspace-relative files/dirs this task is expected to touch (the "
        "``edit_only`` scope)",
    )
    edit_only: bool = Field(
        default=False,
        description="ENFORCE files_hint: an existing file outside it cannot be overwritten (new files "
        "and the language entry file stay allowed). Refine tasks set this so a session fixing one "
        "part cannot rewrite its neighbours.",
    )
    always_writable: list[str] = Field(
        default_factory=list,
        description="files exempt from edit_only (the entry file, and only when the task OWNS it — "
        "see GenerationTask.owns_entry: adding a part means importing it there)",
    )
    read_only: list[str] = Field(
        default_factory=list,
        description="harness-owned files inside write_roots the agent may read but never write "
        "(src/recipes.glsl for glsl_shader: contracts.common.HARNESS_OWNED_SRC); enforced "
        "post-session: a write to one is reverted and the session failed",
    )
    mcp_command: list[str] | None = Field(
        default=None, description="override for the 3dcode MCP server command"
    )


class AgentResult(BaseModel):
    ok: bool
    exit_reason: str = ""  # completed | timeout | error | budget | model_substituted
    text: str = ""
    files_changed: list[FileChange] = Field(default_factory=list)
    transcript_path: str = ""
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0
    tool_calls: int = 0
    turns: int = Field(
        default=0, description="model turns as the backend counts them (claude-code num_turns, codex "
        "turn.completed events, agy num_turns); 0 for gemini-cli, which exposes no turn count",
    )
    errors: list[str] = Field(default_factory=list)
    transient: bool = Field(
        default=False,
        description="the session died on a transient-failure streak (503 / UNAVAILABLE / 429 in the CLI's "
        "own retry loop) and produced nothing — a 503 storm, not the task; the cheap single-shot "
        "path, hedged across keys, may still get through (measured 2026-09-07: 23 of 24 gemini-cli "
        "sessions in one evening)",
    )
