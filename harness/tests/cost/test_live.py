"""LIVE (needs gemini keys): the two measurements docs/COST.md quotes.

    python -m pytest tests/cost/test_live.py -q -m live
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.cost import Block, cache_efficiency, order_blocks, render_blocks
from codeverse.cost.guard import estimate_call, text_tokens

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]


def _stable_text() -> str:
    from codeverse.judges.prompt_builder import build_system_prompt
    from codeverse.judges.rubrics import load_rubric

    return "\n".join([
        build_system_prompt(load_rubric("static_object_v1")),
        (REPO / "codeverse/prompts/blender/contract.md").read_text(),
        (REPO / "codeverse/prompts/blender/cookbook.md").read_text(),
    ])


def _send(model, text: str):
    return model.generate(ChatRequest(messages=[ChatMessage.user(text)], max_output_tokens=1100,
                                      temperature=0.0, thinking="low", label="cost-test")).usage


@pytest.mark.parametrize("arm", ["stable_first", "volatile_first"])
def test_cache_friendly_ordering_is_measurably_cheaper(arm: str):
    """A ≥12k-token stable prefix must cache when it comes FIRST and must not
    when the volatile block precedes it (measured 2026-08-23: 69% vs 0%)."""
    from codeverse.config import get_settings
    from codeverse.models.gemini import GeminiModel

    import uuid

    keys = list(get_settings().gemini_api_keys)
    if not keys:
        pytest.skip("no gemini keys")
    # a corpus no call has seen before: the two arms must not inherit each
    # other's cache (the implicit cache is shared across the whole key pool).
    stable = f"[corpus {uuid.uuid4().hex}]\n" + _stable_text() * 2
    assert text_tokens(stable) > 12_000, "the experiment needs a prefix above the cache floor"
    model = GeminiModel("gemini-3.7-flash", keys=[keys[0 if arm == "stable_first" else 1]])
    usages = []
    for i in range(4):
        blocks = [Block("stable", stable, stable=(arm == "stable_first")),
                  Block("volatile", f"ROUND {i}\nReply with ONE JSON object {{\"n\": {i}}}.")]
        text = render_blocks(order_blocks(blocks)) if arm == "stable_first" \
            else f"ROUND {i}\nReply with ONE JSON object {{\"n\": {i}}}.\n\n{stable}"
        usages.append(_send(model, text))
    cached, total, rate = cache_efficiency(usages[1:])  # the first call warms the cache
    if arm == "stable_first":
        assert rate > 0.3, f"stable-first should cache; got {rate:.2f} (implicit cache is best-effort)"
    else:
        assert rate < 0.2, f"volatile-first must not cache; got {rate:.2f}"


def test_estimate_is_within_15_percent_of_the_bill():
    """The pre-send estimate has to be good enough to route on."""
    from codeverse.models import get_chat_model

    text = _stable_text()[:20_000]
    est = estimate_call("gemini:gemini-3.7-flash", prompt=text, output_tokens=40)
    usage = _send(get_chat_model("gemini:gemini-3.7-flash"),
                  text + "\n\nReply with ONE JSON object {\"ok\": true}.")
    assert abs(est.input_tokens - usage.input_tokens) / usage.input_tokens < 0.15
