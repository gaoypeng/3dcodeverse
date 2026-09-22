"""The three optimisation helpers: pre-send estimate, cache-friendly ordering,
model routing."""

from __future__ import annotations

import pytest

from codeverse3d.cost import estimate_call
from codeverse3d.cost.guard import image_tokens, text_tokens
from codeverse3d.cost.routing import pro_break_even, samples_for_precision


# ------------------------------------------------------------------ guard
def test_estimate_matches_the_price_table():
    est = estimate_call("gemini:gemini-3.7-flash", input_tokens=100_000, cached_tokens=80_000,
                        output_tokens=1_000)
    assert abs(est.usd - (0.015 + 0.006 + 0.00375)) < 1e-9
    assert est.provider == "gemini" and est.model == "gemini-3.7-flash" and not est.approximate


def test_estimate_from_text_and_images():
    est = estimate_call("gemini:gemini-3.1-pro-preview", prompt="x" * 4000, n_images=4, output_tokens=2000)
    assert est.input_tokens == 1000 + image_tokens(4)
    assert est.usd == pytest.approx((est.input_tokens * 2.0 + 2000 * 12.0) / 1e6)
    assert text_tokens(["ab" * 2, "cd" * 2]) == 2
    # a token count and images add up: `cost estimate --in 12000 --images 8` dropped the images
    est = estimate_call("gemini:gemini-3.1-pro-preview", input_tokens=12_000, n_images=8)
    assert est.input_tokens == 12_000 + image_tokens(8)


def test_estimate_of_an_image_model_uses_the_per_image_price():
    est = estimate_call("gemini:gemini-3.1-flash-image", input_tokens=500, n_images_out=2)
    assert est.usd == pytest.approx(500 * 0.5 / 1e6 + 2 * 0.067)


# ------------------------------------------------------------------ caching
def test_pro_judge_pays_for_itself_at_equal_precision():
    assert samples_for_precision("gemini-3.7-flash", 0.030) == 8
    assert samples_for_precision("gemini-3.1-pro-preview", 0.030) == 1
    be = pro_break_even()
    assert be["ratio"] > 3 and be["pro_is_cheaper_by"] > 0.1
