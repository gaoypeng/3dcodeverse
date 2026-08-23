"""Model id parsing + factory.  Ids: ``<provider>:<model>``."""

from __future__ import annotations

from functools import lru_cache

from codeverse.models.base import ChatModel

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
    provider, model = parse_model_id(model_id)
    if provider == "gemini":
        from codeverse.models.gemini import GeminiModel

        return GeminiModel(model)
    if provider == "anthropic":
        from codeverse.models.anthropic import AnthropicModel

        return AnthropicModel(model)
    from codeverse.models.openai import OpenAIModel

    return OpenAIModel(model)
