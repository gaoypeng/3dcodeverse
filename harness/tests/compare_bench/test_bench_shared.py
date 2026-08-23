"""Shared bench plumbing: select_prompts / build_spec / settings-routed backends."""

from __future__ import annotations

import bench.compare_backends as cb
import bench.run_bench as rb
from bench.run_bench import Battery, BenchOptions, build_spec, select_prompts, spec_for
from tests.compare_bench.conftest import BATTERY


def _battery() -> Battery:
    return Battery.load(BATTERY)


def test_select_prompts_ids_tiers_limit():
    b = _battery()
    assert select_prompts(b) == b.prompts
    only = select_prompts(b, ids=[b.prompts[0].id])
    assert [p.id for p in only] == [b.prompts[0].id]
    tiers = {p.tier for p in b.prompts}
    tier = sorted(tiers)[0]
    assert all(p.tier == tier for p in select_prompts(b, tiers=[tier]))
    assert len(select_prompts(b, limit=1)) == 1
    assert select_prompts(b, ids=["nope"]) == []


def test_select_prompts_is_single_owner_for_both_drivers():
    """compare_backends deleted its private copy; both drivers filter identically."""
    b = _battery()
    opts = rb.BenchOptions(ids=[b.prompts[0].id])
    assert rb._select(b, opts) == select_prompts(b, ids=opts.ids)
    assert cb.select_prompts is select_prompts and cb.default_run_track is rb.default_run_track


def test_build_spec_tags_and_budget():
    b = _battery()
    item = b.prompts[0]
    spec = build_spec(b, item, backends=rb.get_settings().backends(), rounds=2, max_usd=1.5,
                      max_minutes=10, tag0="compare", extra_tags=("harness",))
    assert spec.tags[:4] == ["compare", b.name, item.tier, item.category] and "harness" in spec.tags
    assert spec.budget.max_rounds == 2 and spec.budget.max_usd == 1.5
    assert spec.id == f"{b.name}/{item.id}"


def test_spec_for_routes_through_settings_backends():
    b = _battery()
    s = rb.get_settings()
    spec = spec_for(b, b.prompts[0], BenchOptions(generator="gemini-cli:gemini-3.7-flash"))
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    # unset roles fall through to the settings defaults, not the Backends() literals
    assert spec.backends.planner == s.default_planner and spec.backends.judge == s.default_judge
    assert spec.backends.captioner == s.default_captioner
