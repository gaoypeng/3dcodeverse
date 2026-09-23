"""Estimate what a call will cost **before** sending it, and pick a tier.

The ledger (``telemetry/cost.jsonl``) records money *after* it is spent, and
``orchestrator.BudgetGuard`` is only the wall clock.  This is the cheap estimate that belongs in front of a
call: how many tokens am I about to send, and what will they cost on this model?

Token counts are estimates (≈4 characters per token for prose/code, a flat
per-image count for vision parts) — good to ±15% for prompt sizing, which is all
a routing decision needs.  When the exact number matters, price the ``Usage``
the provider returns (``codeverse3d.cost.record_call``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from codeverse3d.cost.types import normalise_ids
from codeverse3d.models.pricing import per_image_usd, price_provenance, unit_prices

#: characters per token — Gemini/OpenAI English prose and code both sit near 4
CHARS_PER_TOKEN = 4.0

#: tokens billed for one image part, by longest edge (Gemini bills 1290 for a
#: 1024² tile; smaller images are cheaper, larger ones are tiled)
IMAGE_TOKENS: dict[int, int] = {256: 258, 512: 258, 768: 1032, 1024: 1290, 2048: 5160}


def text_tokens(text: str | Iterable[str]) -> int:
    """Estimated prompt tokens for one string or a bag of blocks."""
    if isinstance(text, str):
        return int(len(text) / CHARS_PER_TOKEN + 0.5)
    return sum(text_tokens(t) for t in text)


def image_tokens(n: int = 1, *, px: int = 1024) -> int:
    """Estimated input tokens for ``n`` images whose longest edge is ``px``."""
    step = min(IMAGE_TOKENS, key=lambda k: abs(k - px))
    return n * IMAGE_TOKENS[step]


@dataclass(frozen=True)
class CostEstimate:
    """What one call is expected to cost, and how much to trust the number."""

    model_id: str
    provider: str
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    n_images_out: int = 0
    usd: float = 0.0
    price_source: str = "unknown"
    approximate: bool = True

    def line(self) -> str:
        flag = " (approx)" if self.approximate else ""
        return (f"{self.model_id}: ~{self.input_tokens:,} in ({self.cached_tokens:,} cached) "
                f"+ {self.output_tokens:,} out ⇒ ${self.usd:.4f}{flag}")


def estimate_call(
    model_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    prompt: str | Sequence[str] = "",
    n_images: int = 0,
    n_images_out: int = 0,
) -> CostEstimate:
    """Price a call that has not happened yet.

    ``model_id`` is any harness id (``gemini:gemini-3.7-flash``,
    ``api-agent:gemini:gemini-3.7-flash``).  Give either token counts or the
    ``prompt`` text, plus ``n_images`` vision parts (1024 px) either way."""
    kind, provider, model = normalise_ids("", model_id)
    if not provider:
        kind, provider, model = normalise_ids(model_id, model_id)
    tokens_in = (input_tokens or text_tokens(prompt)) + image_tokens(n_images)
    cached = max(0, min(cached_tokens, tokens_in))
    p_in, p_cached, p_out = unit_prices(provider, model, prompt_tokens=tokens_in)
    usd = ((tokens_in - cached) * p_in + cached * p_cached + output_tokens * p_out) / 1e6
    if n_images_out:
        usd += n_images_out * per_image_usd(provider, model)
    row = price_provenance(provider, model)
    return CostEstimate(model_id=model_id, provider=provider, model=model, input_tokens=tokens_in,
                        cached_tokens=cached, output_tokens=output_tokens, n_images_out=n_images_out,
                        usd=usd, price_source=row.match if row.price else "unknown",
                        approximate=row.approximate)

