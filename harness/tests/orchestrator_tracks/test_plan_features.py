"""``tracks/plan_features.py``: what an A/B rig may assume about a ``C3D_*`` switch (CQ-5).

Which names are live is derived from ``Settings`` — every switch the harness reads is a
field there — so no list can drift from the code and no test has to grep the tree.
"""

from __future__ import annotations

from codeverse3d.config import Settings
from codeverse3d.tracks.plan_features import (
    GENERATION_SIDE_ENV,
    NODE_SIDE_ENV,
    dead_env_keys,
    pin_plan_blockers,
)


def test_every_settings_field_is_a_live_switch_and_anything_else_is_dead():
    """An arm that differs only by a name nothing reads is byte-identical to its control
    (one A/B printed "keep, mean delta +0.344" for two such arms), so ab_plan refuses it.
    Every field, nested field, flat spelling and node-side name is live; the retired ones
    are dead the moment their field goes."""
    live = [f"C3D_{name.upper()}" for name in Settings.model_fields]
    live += [f"C3D_{section.upper()}__{name.upper()}" for section in ("rate", "render", "limits", "judge")
             for name in Settings.model_fields[section].annotation.model_fields]
    live += [*Settings.FLAT, *NODE_SIDE_ENV, *GENERATION_SIDE_ENV]
    assert dead_env_keys(dict.fromkeys(live, "1")) == []
    retired = ["C3D_PLAN_FEATURES", "C3D_FEWER_TURNS", "C3D_DETAIL_ROUNDS", "C3D_RATE__STORM_GATE",
               "C3D_RATE__TPM_PER_KEY", "C3D_MYSTERY_KNOB"]
    assert dead_env_keys({**dict.fromkeys(retired, "1"), "C3D_PLAN_BRIEF": "off"}) == sorted(retired)
    assert dead_env_keys({"GEMINI_CLI_HOME": "/x"}) == [], "outside the C3D_ family nothing is judged dead"


# --------------------------------------------------------------------- pin-plan safety
def test_a_generation_side_switch_may_share_one_plan():
    """A switch that acts after planning cannot change the plan, so both arms can be
    seeded with the same plan.json and the paired delta stops carrying the planner's
    spread — the dominant variance term (eval/docs/EVAL.md §8.1)."""
    for name in sorted(GENERATION_SIDE_ENV):
        assert pin_plan_blockers({name: "1"}) == [], name
    assert pin_plan_blockers({}) == []


def test_a_dead_switch_blocks_nothing_but_a_live_neighbour_still_does():
    """No code reads C3D_PLAN_FEATURES, so it cannot change a plan: it never blocks
    --pin-plan (until 2026-09-22 its six never-implemented feature names did)."""
    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "fit"}) == []
    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "all", "C3D_PLAN_BRIEF": "off"}) == [
        "C3D_PLAN_BRIEF is not known to act after planning"]


def test_an_unclassified_switch_defaults_to_refusing():
    """Refusing to pin costs one noisy A/B; pinning wrongly costs a confident wrong
    answer.  So anything live that is not known to act after planning refuses — a
    plan-side field, or a variable some CLI may read."""
    assert pin_plan_blockers({"C3D_PLAN_BRIEF": "off"}) == [
        "C3D_PLAN_BRIEF is not known to act after planning"]
    assert pin_plan_blockers({"GEMINI_CLI_HOME": "/x"}) == [
        "GEMINI_CLI_HOME is not known to act after planning"]
