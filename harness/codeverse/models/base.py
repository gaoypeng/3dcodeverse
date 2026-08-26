"""The ChatModel protocol every API backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codeverse.contracts.chat import ChatRequest, ChatResponse


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
        * return tool calls when ``request.tools`` is given and the model calls one,
        * fill ``usage`` including ``cost_usd`` (see ``pricing.py``),
        * raise ``ModelError`` (retryable=True/False) rather than provider exceptions.
        """
        ...

    def supports_vision(self) -> bool: ...


class ModelError(RuntimeError):
    """A model call failed.  ``attempts`` is how many round-trips the retry machine
    issued before giving up (0 = unknown / not a retried call); the cost ledger
    records it on the error row (``docs/COST.md`` §24)."""

    def __init__(
        self, message: str, *, retryable: bool = False, status: int | None = None, attempts: int = 0
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.attempts = attempts
