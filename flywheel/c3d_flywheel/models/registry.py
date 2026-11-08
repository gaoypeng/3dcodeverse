"""Model id parsing + factory.  Ids: ``<provider>:<model>``.

Every model handed out here is wrapped by ``c3d_flywheel.cost.instrument`` so each
call lands in the cost ledger (the run's ``telemetry/cost.jsonl`` when a run is
active, otherwise a per-process log) — always: the ledger is the only record of money."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from c3d_flywheel.models.base import ChatModel

PROVIDERS = ("gemini", "anthropic", "openai")


def parse_model_id(model_id: str) -> tuple[str, str]:
    if ":" not in model_id:
        raise ValueError(f"model id must be '<provider>:<model>', got {model_id!r}")
    provider, model = model_id.split(":", 1)
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; known: {PROVIDERS}")
    if not model.strip():
        raise ValueError(f"model id {model_id!r} names no model")
    return provider, model


@lru_cache(maxsize=32)
def get_chat_model(model_id: str) -> ChatModel:
    """The (metered) ChatModel for ``<provider>:<model>``."""
    from c3d_flywheel.cost.instrument import metered_chat_model

    return metered_chat_model(build_chat_model(model_id))


def build_chat_model(model_id: str, **kw: Any) -> ChatModel:
    """The bare (unmetered) model for ``<provider>:<model>``; ``kw`` goes to its constructor."""
    provider, model = parse_model_id(model_id)
    if provider == "gemini":
        from c3d_flywheel.models.gemini import GeminiModel

        return GeminiModel(model, **kw)
    if provider == "anthropic":
        from c3d_flywheel.models.anthropic import AnthropicModel

        return AnthropicModel(model, **kw)
    from c3d_flywheel.models.openai import OpenAIModel

    return OpenAIModel(model, **kw)
