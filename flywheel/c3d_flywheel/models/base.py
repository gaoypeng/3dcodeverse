"""The ChatModel protocol every API backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codeverse3d.contracts.chat import ChatRequest, ChatResponse
from codeverse3d.contracts.common import Usage


@runtime_checkable
class ChatModel(Protocol):
    """One provider+model.  Implementations must be thread-safe (the
    orchestrator fans out judge/caption calls across threads)."""

    provider: str  # gemini | anthropic | openai
    model: str

    @property
    def id(self) -> str: ...

    def generate(self, request: ChatRequest) -> ChatResponse:
        """Blocking call.  Must:
        * honour ``request.response_schema`` (return ``parsed`` as a dict/list),
        * attach images (``ImagePart``) inline in order,
        * fill ``usage`` including ``cost_usd`` (see ``pricing.py``),
        * raise ``ModelError`` (retryable=True/False) rather than provider exceptions.
        """
        ...


class ModelError(RuntimeError):
    """A model call failed.  ``attempts`` = round-trips issued (0 = unknown); ``usage`` =
    what the provider already billed for the failed call (empty when nothing was)."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        attempts: int = 0,
        usage: Usage | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.attempts = attempts
        self.usage = usage if usage is not None else Usage()
