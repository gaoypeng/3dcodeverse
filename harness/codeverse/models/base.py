"""The ChatModel protocol every API backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Usage


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
    """A model call failed.  ``attempts`` is how many round-trips the retry machine
    issued before giving up (0 = unknown / not a retried call); the cost ledger
    records it on the error row (``docs/COST.md`` §24).  ``usage`` is what the
    provider ALREADY billed for the failed call — a bad-JSON reply is charged like
    a good one — so the ledger and the key pool's TPM accounting can see real
    consumption instead of assuming a failure was free (mirrors
    ``tracks.planner.PlanningError.usage``); empty when nothing was billed."""

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
