"""Model id parsing + factory.  Ids: ``<provider>:<model>``.

Every model handed out here is wrapped by ``codeverse3d.cost.instrument`` so each
call lands in the cost ledger (the run's ``telemetry/cost.jsonl`` when a run is
active, otherwise a per-process log) — always: the ledger is the only record of money."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from codeverse3d.models.base import ChatModel

PROVIDERS = ("gemini", "anthropic", "openai")


def parse_model_id(model_id: str) -> tuple[str, str]:
    if ":" not in model_id:
        raise ValueError(f"model id must be '<provider>:<model>', got {model_id!r}")
    provider, model = model_id.split(":", 1)
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; known: {PROVIDERS}")
    return provider, model


@lru_cache(maxsize=32)
def get_chat_model(model_id: str) -> ChatModel:
    """The (metered) ChatModel for ``<provider>:<model>``."""
    from codeverse3d.cost.instrument import metered_chat_model

    return metered_chat_model(build_chat_model(model_id))


def build_chat_model(model_id: str, **kw: Any) -> ChatModel:
    """The bare (unmetered) model for ``<provider>:<model>``; ``kw`` goes to its constructor."""
    provider, model = parse_model_id(model_id)
    if provider == "gemini":
        from codeverse3d.models.gemini import GeminiModel

        return GeminiModel(model, **kw)
    if provider == "anthropic":
        from codeverse3d.models.anthropic import AnthropicModel

        return AnthropicModel(model, **kw)
    from codeverse3d.models.openai import OpenAIModel

    return OpenAIModel(model, **kw)
