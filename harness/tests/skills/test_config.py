"""The switches: read at call time, tolerant of garbage, and registered as LIVE.

``tracks/plan_features.py`` exists because a wave A/B'd a switch nothing read and printed
"keep, mean delta +0.344" for two byte-identical arms.  These tests are what keep
``CV3D_SKILLS`` from becoming that switch.
"""

from __future__ import annotations

import pytest

from codeverse.skills import config as C
from codeverse.tracks import plan_features as F


def test_off_by_default(monkeypatch):
    for env in (C.SKILLS_ENV, C.SKILLS_MAX_ENV, C.SKILLS_UNVERIFIED_ENV):
        monkeypatch.delenv(env, raising=False)
    assert C.skills_enabled() is False
    assert C.skills_unverified() is False
    assert C.skills_max() == C.DEFAULT_SKILLS_MAX == 5


@pytest.mark.parametrize("raw, want", [("on", True), ("1", True), ("true", True), ("YES", True),
                                       ("off", False), ("0", False), ("", False), ("maybe", False)])
def test_flag_spellings(monkeypatch, raw, want):
    monkeypatch.setenv(C.SKILLS_ENV, raw)
    assert C.skills_enabled() is want


def test_read_at_call_time_not_cached(monkeypatch):
    monkeypatch.setenv(C.SKILLS_ENV, "on")
    assert C.skills_enabled()
    monkeypatch.setenv(C.SKILLS_ENV, "off")
    assert not C.skills_enabled()


@pytest.mark.parametrize("raw, want", [("3", 3), ("0", 0), ("-1", 0), ("nope", 5), ("", 5)])
def test_max_is_never_a_crash(monkeypatch, raw, want):
    monkeypatch.setenv(C.SKILLS_MAX_ENV, raw)
    assert C.skills_max() == want


def test_the_switches_are_registered_as_live_not_dead():
    for env in (C.SKILLS_ENV, C.SKILLS_MAX_ENV, C.SKILLS_UNVERIFIED_ENV):
        assert env in F.LIVE_SWITCHES, f"{env} must be declared so ab_plan accepts an arm using it"
        assert env not in F.DEAD_SWITCHES
        assert F.LIVE_SWITCHES[env] == "codeverse/skills/config.py"
    assert F.dead_env_keys({C.SKILLS_ENV: "on"}) == []
