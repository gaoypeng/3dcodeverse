"""Estimate what a call will cost **before** sending it, and pick a tier.

The budget guard in ``orchestrator/budget.py`` stops a run *after* the money is
gone.  This is the cheap check that belongs in front of a call: how many tokens
am I about to send, what will they cost on this model, and is there a cheaper
model that still fits the job?

Token counts are estimates (≈4 characters per token for prose/code, a flat
per-image count for vision parts) — good to ±15% for prompt sizing, which is all
a routing decision needs.  When the exact number matters, price the ``Usage``
the provider returns (``codeverse.cost.record_call``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from codeverse.cost.types import normalise_ids
from codeverse.models.pricing import per_image_usd, price_provenance, unit_prices

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

    @property
    def known(self) -> bool:
        return self.price_source != "unknown"

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
    image_px: int = 1024,
    n_images_out: int = 0,
    image_out_px: int = 1024,
) -> CostEstimate:
    """Price a call that has not happened yet.

    ``model_id`` is any harness id (``gemini:gemini-3.7-flash``,
    ``api-agent:gemini:gemini-3.7-flash``).  Give either token counts or the
    ``prompt`` text (plus ``n_images`` vision parts)."""
    kind, provider, model = normalise_ids("", model_id)
    if not provider:
        kind, provider, model = normalise_ids(model_id, model_id)
    tokens_in = input_tokens or (text_tokens(prompt) + image_tokens(n_images, px=image_px) if prompt or n_images else 0)
    cached = max(0, min(cached_tokens, tokens_in))
    p_in, p_cached, p_out = unit_prices(provider, model, prompt_tokens=tokens_in)
    usd = ((tokens_in - cached) * p_in + cached * p_cached + output_tokens * p_out) / 1e6
    if n_images_out:
        usd += n_images_out * per_image_usd(provider, model, size=image_out_px)
    row = price_provenance(provider, model)
    return CostEstimate(model_id=model_id, provider=provider, model=model, input_tokens=tokens_in,
                        cached_tokens=cached, output_tokens=output_tokens, n_images_out=n_images_out,
                        usd=usd, price_source=row.match if row.price else "unknown",
                        approximate=row.approximate)


@dataclass
class CostGuard:
    """A per-run (or per-stage) allowance checked *before* each call.

    ``spent`` is advanced by :meth:`commit` with what the call really cost, so
    the guard converges on the truth even when the estimate was off."""

    budget_usd: float
    spent_usd: float = 0.0
    reserve_usd: float = 0.0  # keep this much back for the judge / finalise
    calls: int = 0
    rejected: list[str] = field(default_factory=list)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.reserve_usd - self.spent_usd)

    def affordable(self, estimate: CostEstimate) -> bool:
        return estimate.usd <= self.remaining_usd

    def check(self, estimate: CostEstimate) -> tuple[bool, str]:
        """``(allow, reason)``.  An unpriced model is allowed but flagged: refusing
        to run because we cannot price a model would be worse than running it."""
        if not estimate.known:
            return True, f"no price row for {estimate.model_id}: cost unknown, not blocked"
        if self.affordable(estimate):
            return True, ""
        msg = (f"{estimate.model_id} would cost ${estimate.usd:.4f} but only "
               f"${self.remaining_usd:.4f} is left of ${self.budget_usd:.2f}")
        self.rejected.append(msg)
        return False, msg

    def commit(self, usd: float) -> None:
        self.spent_usd += max(0.0, usd)
        self.calls += 1


def cheapest_affordable(
    model_ids: Sequence[str],
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    budget_usd: float | None = None,
) -> tuple[str, CostEstimate] | None:
    """Pick the cheapest model in ``model_ids`` that fits ``budget_usd``.

    ``model_ids`` should be ordered best-quality-first; the caller decides how
    much quality it is prepared to trade (see :mod:`codeverse.cost.routing`)."""
    scored: list[tuple[float, str, CostEstimate]] = []
    for mid in model_ids:
        est = estimate_call(mid, input_tokens=input_tokens, output_tokens=output_tokens,
                            cached_tokens=cached_tokens)
        if not est.known:
            continue
        if budget_usd is not None and est.usd > budget_usd:
            continue
        scored.append((est.usd, mid, est))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0])
    return scored[0][1], scored[0][2]
