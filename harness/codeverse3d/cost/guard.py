"""Estimate what a call will cost before sending it (token counts are ~4 chars/token, +-15%)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from codeverse3d.contracts.common import Usage
from codeverse3d.cost.types import normalise_ids
from codeverse3d.models.pricing import estimate_cost, price_provenance

CHARS_PER_TOKEN = 4.0

#: tokens billed for one image part, by longest edge
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

    model_id: str
    provider: str
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
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
) -> CostEstimate:
    """Price a call that has not happened yet, from token counts or ``prompt`` text plus ``n_images``."""
    kind, provider, model = normalise_ids("", model_id)
    if not provider:
        kind, provider, model = normalise_ids(model_id, model_id)
    tokens_in = (input_tokens or text_tokens(prompt)) + image_tokens(n_images)
    cached = max(0, min(cached_tokens, tokens_in))
    usd = estimate_cost(provider, model, Usage(input_tokens=tokens_in, cached_tokens=cached, output_tokens=output_tokens))
    row = price_provenance(provider, model)
    return CostEstimate(model_id=model_id, provider=provider, model=model, input_tokens=tokens_in,
                        cached_tokens=cached, output_tokens=output_tokens,
                        usd=usd, price_source=row.match if row.price else "unknown",
                        approximate=row.approximate)

