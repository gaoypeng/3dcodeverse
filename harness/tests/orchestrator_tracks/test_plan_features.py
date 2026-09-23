"""``tracks/plan_features.py``: what an A/B rig may assume about a ``C3D_*`` switch."""

from __future__ import annotations

from codeverse3d.config import Settings
from codeverse3d.tracks.plan_features import (
    GENERATION_SIDE_ENV,
    NODE_SIDE_ENV,
    dead_env_keys,
    pin_plan_blockers,
)


def test_every_settings_field_is_a_live_switch_and_anything_else_is_dead():
    live = [f"C3D_{name.upper()}" for name in Settings.model_fields]
    live += [f"C3D_{section.upper()}__{name.upper()}" for section in ("rate", "render", "limits", "judge")
             for name in Settings.model_fields[section].annotation.model_fields]
    live += [*Settings.FLAT, *NODE_SIDE_ENV, *GENERATION_SIDE_ENV]
    assert dead_env_keys(dict.fromkeys(live, "1")) == []
    retired = ["C3D_PLAN_FEATURES", "C3D_FEWER_TURNS", "C3D_DETAIL_ROUNDS", "C3D_RATE__STORM_GATE",
               "C3D_RATE__TPM_PER_KEY", "C3D_MYSTERY_KNOB"]
    assert dead_env_keys({**dict.fromkeys(retired, "1"), "C3D_PLAN_BRIEF": "off"}) == sorted(retired)
    assert dead_env_keys({"GEMINI_CLI_HOME": "/x"}) == [], "outside the C3D_ family nothing is judged dead"


def test_pin_plan_refuses_only_live_switches_not_known_to_act_after_planning():
    assert pin_plan_blockers({}) == []
    for name in sorted(GENERATION_SIDE_ENV):
        assert pin_plan_blockers({name: "1"}) == [], name
    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "fit"}) == [], "a dead switch cannot change a plan"
    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "all", "C3D_PLAN_BRIEF": "off"}) == [
        "C3D_PLAN_BRIEF is not known to act after planning"]
    assert pin_plan_blockers({"GEMINI_CLI_HOME": "/x"}) == [
        "GEMINI_CLI_HOME is not known to act after planning"]
