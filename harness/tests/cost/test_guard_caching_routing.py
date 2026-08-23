"""The three optimisation helpers: pre-send estimate, cache-friendly ordering,
model routing."""

from __future__ import annotations

import pytest

from codeverse.contracts.common import Usage
from codeverse.cost import (
    Block,
    CostGuard,
    cache_efficiency,
    cheapest_affordable,
    estimate_call,
    order_blocks,
    prefix_report,
    prefix_signature,
    render_blocks,
    text_tokens,
)
from codeverse.cost.guard import image_tokens
from codeverse.cost.routing import ROUTES, default_route, pro_break_even, samples_for_precision
from codeverse.cost.types import Role


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


def test_estimate_of_an_image_model_uses_the_per_image_price():
    est = estimate_call("gemini:gemini-3.1-flash-image", input_tokens=500, n_images_out=2)
    assert est.usd == pytest.approx(500 * 0.5 / 1e6 + 2 * 0.067)


def test_guard_blocks_only_what_it_can_price():
    guard = CostGuard(budget_usd=0.10, reserve_usd=0.02)
    cheap = estimate_call("gemini:gemini-3.7-flash", input_tokens=10_000, output_tokens=500)
    allow, why = guard.check(cheap)
    assert allow and not why
    guard.commit(cheap.usd)
    dear = estimate_call("gemini:gemini-3.1-pro-preview", input_tokens=2_000_000, output_tokens=50_000)
    allow, why = guard.check(dear)
    assert not allow and "left of" in why and guard.rejected
    unknown = estimate_call("gemini:no-such-model", input_tokens=10_000_000, output_tokens=1)
    assert guard.check(unknown)[0] is True  # unpriceable ≠ blocked


def test_cheapest_affordable_picks_a_tier():
    pick = cheapest_affordable(
        ["gemini:gemini-3.1-pro-preview", "gemini:gemini-3.7-flash", "gemini:gemini-3.1-flash-lite"],
        input_tokens=50_000, output_tokens=2_000)
    assert pick is not None and pick[0] == "gemini:gemini-3.1-flash-lite"
    assert cheapest_affordable(["gemini:gemini-3.1-pro-preview"], input_tokens=10_000_000,
                               output_tokens=0, budget_usd=0.001) is None


# ------------------------------------------------------------------ caching
def test_order_blocks_puts_the_stable_prefix_first():
    blocks = [Block("round", "round 3 of 4", order=1),
              Block("system", "SYSTEM RULES", stable=True, order=0),
              Block("rubric", "RUBRIC", stable=True, order=1),
              Block("renders", "<images>", order=0)]
    ordered = order_blocks(blocks)
    assert [b.name for b in ordered] == ["system", "rubric", "renders", "round"]
    assert render_blocks(ordered).startswith("SYSTEM RULES\n\nRUBRIC")


def test_prefix_signature_only_covers_the_stable_head():
    a = order_blocks([Block("s", "SYS", stable=True), Block("v", "round 1")])
    b = order_blocks([Block("s", "SYS", stable=True), Block("v", "round 2")])
    assert prefix_signature(a) == prefix_signature(b)
    c = order_blocks([Block("s", "SYS!", stable=True), Block("v", "round 1")])
    assert prefix_signature(c) != prefix_signature(a)


def test_prefix_report_measures_the_shared_head_and_its_value():
    stable = "S" * 40_000  # 10k tokens
    prompts = [stable + f"\nround {i}" for i in range(4)]
    rep = prefix_report(prompts)
    assert rep.prefix_tokens == pytest.approx(10_000, rel=0.01)
    assert rep.shared_fraction > 0.99
    # 3 later calls read 10k tokens from cache instead of paying full input price
    assert rep.savings_usd(0.75, 0.075) == pytest.approx(3 * 10_000 * 0.675 / 1e6, rel=0.01)
    volatile_first = [f"round {i}\n" + stable for i in range(4)]
    assert prefix_report(volatile_first).prefix_tokens < 10  # the cache is gone


def test_cache_efficiency_reads_usages():
    us = [Usage(input_tokens=100, cached_tokens=0), Usage(input_tokens=100, cached_tokens=80)]
    cached, total, rate = cache_efficiency(us)
    assert (cached, total) == (80, 200) and rate == 0.4


# ------------------------------------------------------------------ routing
def test_routing_defaults_exist_for_every_role():
    for role in (Role.PLANNER, Role.GENERATOR, Role.JUDGE, Role.IMAGE, Role.CAPTIONER):
        route = default_route(role)
        assert route is not None and route.model_id and route.when, role
    assert all(r.usd_per_call > 0 for r in ROUTES)


def test_pro_judge_pays_for_itself_at_equal_precision():
    assert samples_for_precision("gemini-3.7-flash", 0.030) == 8
    assert samples_for_precision("gemini-3.1-pro-preview", 0.030) == 1
    be = pro_break_even()
    assert be["ratio"] > 3 and be["pro_is_cheaper_by"] > 0.1
