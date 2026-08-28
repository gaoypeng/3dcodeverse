"""ChatModel request/response shapes — provider-neutral."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse.contracts.common import Usage


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    path: str | None = None
    data_b64: str | None = None
    mime: str = "image/png"
    label: str = ""


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    content: str
    images: list[ImagePart] = Field(default_factory=list)
    is_error: bool = False


Part = TextPart | ImagePart | ToolCallPart | ToolResultPart


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    parts: list[Part]

    @classmethod
    def user(cls, text: str, images: list[ImagePart] | None = None) -> ChatMessage:
        parts: list[Part] = [TextPart(text=text)]
        parts.extend(images or [])
        return cls(role="user", parts=parts)

    @classmethod
    def assistant(cls, text: str) -> ChatMessage:
        return cls(role="assistant", parts=[TextPart(text=text)])

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart))


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(description="JSON schema of the arguments object")


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    system: str = ""
    response_schema: dict[str, Any] | None = Field(default=None, description="force JSON matching this schema")
    tools: list[ToolSpec] | None = None
    temperature: float = 0.7
    #: 65 536 is the declared output limit of every gemini-3.x model the harness uses
    #: (models.get: input 1 048 576 / output 65 536).  It is a CEILING, not a reservation —
    #: only tokens actually produced are billed — and a low one silently truncates a
    #: thinking model mid-answer: a 15 359-token thought hit the old 16 000 default at
    #: 15 996 and the turn arrived with no tool call (art_med_tool_chest, 2026-08-27).
    max_output_tokens: int = 65_536
    thinking: Literal["off", "low", "medium", "high"] = "low"
    label: str = Field(default="", description="for logs/cost ledger: planner / judge / generate ...")
    max_wait_s: float | None = Field(
        default=None,
        gt=0,
        description="the longest this ONE logical call may spend, retries and their waits included; "
        "None = the model's default (models.retry.RETRY_DEADLINE_S).  A caller clips it to what it "
        "can still afford (owner 2026-08-27: judge-sample and planner floors are 900 s).",
    )


class ChatResponse(BaseModel):
    text: str = ""
    parsed: Any = None
    tool_calls: list[ToolCallPart] = Field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = Field(default_factory=Usage)
    raw: dict[str, Any] = Field(default_factory=dict)
