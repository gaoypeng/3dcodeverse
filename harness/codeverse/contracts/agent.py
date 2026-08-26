"""CodingAgent job/result — one headless agentic session on a workspace."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from codeverse.contracts.chat import ImagePart
from codeverse.contracts.common import Usage


class FileChange(BaseModel):
    path: str
    status: str  # added | modified | deleted
    lines_added: int = 0
    lines_removed: int = 0


class ApiAgentOptions(BaseModel):
    """Knobs honoured only by the in-process ``api-agent`` backend."""

    max_usd: float = Field(
        default=0.0, description="stop the session when its own cost exceeds this (0 = no cap)"
    )
    temperature: float = 0.3
    thinking: str = "low"
    allow_shell: bool = True


#: legacy ``AgentJob.extra`` keys lifted into the typed fields (values stay in extra too)
_LEGACY_JOB_KEYS = ("round", "kind", "language", "track", "files_hint", "mcp_command")
_LEGACY_API_KEYS = ("max_usd", "temperature", "thinking", "allow_shell")


class AgentJob(BaseModel):
    workspace: str
    prompt: str
    system_append: str = ""
    model: str = ""
    label: str = ""
    timeout_s: int = 1800
    max_turns: int = 60
    allow_network: bool = False
    spatial_tools: bool = Field(
        default=True, description="expose the 3dcv MCP spatial tools to the agent"
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
        description="workspace-relative files/dirs this task is expected to touch — attributes "
        "files_changed between concurrent sessions in one workspace",
    )
    edit_only: bool = Field(
        default=False,
        description="ENFORCE files_hint: an existing file outside it cannot be overwritten (new files "
        "and the language entry file stay allowed). Refine tasks set this so a session fixing one "
        "part cannot rewrite its neighbours.",
    )
    images: list[ImagePart] = Field(
        default_factory=list,
        description="inline images for the FIRST user message (reference photos; the contact sheet the "
        "judge scored). Backends without an image channel ignore them; the prompt names the files too.",
    )
    always_writable: list[str] = Field(
        default_factory=list,
        description="files exempt from edit_only (the entry file: adding a part means importing it there)",
    )
    read_only: list[str] = Field(
        default_factory=list,
        description="harness-owned files inside write_roots the agent may read but never write "
        "(src/recipes.glsl for glsl_shader: contracts.common.HARNESS_OWNED_SRC); a write_file / "
        "edit_file on one is refused with 'harness-owned — call its functions instead'",
    )
    mcp_command: list[str] | None = Field(
        default=None, description="override for the 3dcv MCP server command"
    )
    api: ApiAgentOptions = Field(default_factory=ApiAgentOptions)
    extra: dict[str, Any] = Field(
        default_factory=dict, description="one-off backend hints (legacy keys are lifted)"
    )

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_extra(cls, data: Any) -> Any:
        """Constructors that pass ``extra={"round": 2, ...}`` keep working: known keys
        are lifted into the typed fields.  ``extra`` itself is left untouched so
        existing ``job.extra[...]`` readers see exactly what they were given."""
        if not isinstance(data, dict):
            return data
        extra = data.get("extra")
        if not isinstance(extra, dict):
            return data
        data = dict(data)  # never mutate the caller's dict
        for key in _LEGACY_JOB_KEYS:
            if key in extra and key not in data:
                data[key] = extra[key]
        if "api" not in data:
            lifted = {k: extra[k] for k in _LEGACY_API_KEYS if k in extra}
            if lifted:
                data["api"] = lifted
        return data


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
