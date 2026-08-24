"""How many tokens is this request about to spend?  (TPM-aware key scheduling.)

The :class:`~codeverse.models.keypool.KeyPool` schedules against a per-key
tokens-per-minute quota, so it needs a number *before* the call: a 200 k-token
judge verdict and a 2 k-token caption must not be admitted on the same terms.

``request_tokens`` reuses the estimator the cost guard already ships
(:func:`codeverse.cost.guard.estimate_call` — ~4 characters per token plus a flat
count per image part) and returns **input** tokens only.  That is what a Gemini
key's TPM meter bills, and it is also what dominates: over the recorded corpus
(6 021 priced calls, ``docs/COST.md`` Part III) output + thoughts were 2 % of
input, so an output term would only add noise.

Estimates are good to about ±15 %.  The pool reconciles against the provider's
own prompt-token count when the call returns
(``KeyPool.report(..., tokens=actual, reserved=hint)``), so a bad estimate costs
one minute of slightly-wrong pacing, never correctness.
"""

from __future__ import annotations

from codeverse.contracts.chat import ChatRequest, ImagePart, TextPart, ToolResultPart

#: longest edge we assume for an image part whose size we do not measure; the
#: harness caps judge payloads at ``Settings.judge.max_px`` = 1024 (docs/COST.md §3)
DEFAULT_IMAGE_PX = 1024


def request_parts(request: ChatRequest) -> tuple[list[str], int]:
    """``(text blocks, number of image parts)`` of a request, system prompt and
    tool schemas included — everything the provider will count as prompt."""
    blocks: list[str] = []
    images = 0
    if request.system:
        blocks.append(request.system)
    for tool in request.tools or ():
        blocks.append(f"{tool.name}{tool.description}{tool.parameters}")
    if request.response_schema is not None:
        blocks.append(str(request.response_schema))
    for msg in request.messages:
        for part in msg.parts:
            if isinstance(part, TextPart):
                blocks.append(part.text)
            elif isinstance(part, ImagePart):
                images += 1
            elif isinstance(part, ToolResultPart):
                blocks.append(part.content)
                images += len(part.images)
            else:  # ToolCallPart
                blocks.append(f"{part.name}{part.arguments}")
    return blocks, images


def request_tokens(request: ChatRequest, *, model_id: str = "", image_px: int = DEFAULT_IMAGE_PX) -> int:
    """Estimated **input** tokens of ``request`` (never negative)."""
    from codeverse.cost.guard import estimate_call

    blocks, images = request_parts(request)
    est = estimate_call(model_id or "gemini:unknown", prompt=blocks, n_images=images, image_px=image_px)
    return max(0, est.input_tokens)
