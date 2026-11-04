"""ChatModel request/response shapes — provider-neutral."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse3d.contracts.common import Usage


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    path: str | None = None
    mime: str = "image/png"
    label: str = ""


Part = TextPart | ImagePart


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
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


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    system: str = ""
    response_schema: dict[str, Any] | None = Field(default=None, description="force JSON matching this schema")
    temperature: float = 0.7
    #: A ceiling, not a reservation: a low one truncates a thinking model mid-answer.
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
    finish_reason: str = ""
    usage: Usage = Field(default_factory=Usage)
    raw: dict[str, Any] = Field(default_factory=dict)
