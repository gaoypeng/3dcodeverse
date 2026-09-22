"""The switches: read at call time, tolerant of garbage, and registered as LIVE.

``tracks/plan_features.py`` exists because a wave A/B'd a switch nothing read and printed
"keep, mean delta +0.344" for two byte-identical arms.  These tests are what keep
``C3D_SKILLS`` from becoming that switch.
"""

from __future__ import annotations

import pytest

from codeverse3d.skills import config as C
from codeverse3d.tracks import plan_features as F


def test_on_by_default(monkeypatch):
    """Flipped 2026-09-22 (owner): every backend is a vendor CLI with a native skill loader,
    and the whole library routes — the cap of 5 per session is the cost knob, not the switch."""
    for env in (C.SKILLS_ENV, C.SKILLS_MAX_ENV, C.SKILLS_UNVERIFIED_ENV):
        monkeypatch.delenv(env, raising=False)
    assert C.skills_enabled() is True
    assert C.skills_unverified() is True
    assert C.skills_max() == C.DEFAULT_SKILLS_MAX == 5


@pytest.mark.parametrize("raw, want", [("on", True), ("1", True), ("true", True), ("YES", True),
                                       ("off", False), ("0", False), ("", True), ("maybe", False)])
def test_flag_spellings(monkeypatch, raw, want):
    """Unset or empty is the default (ON); garbage is OFF, never the variant — a typo in a
    bench command must produce a control run (``config.env_flag``)."""
    monkeypatch.setenv(C.SKILLS_ENV, raw)
    assert C.skills_enabled() is want


@pytest.mark.parametrize("raw", ["0", "off", "false", "no"])
def test_c3d_skills_0_turns_both_off(monkeypatch, raw):
    """The ONE off switch an A/B control arm sets: nothing is enabled, whatever UNVERIFIED says."""
    monkeypatch.setenv(C.SKILLS_ENV, raw)
    monkeypatch.delenv(C.SKILLS_UNVERIFIED_ENV, raising=False)
    assert C.skills_enabled() is False


def test_unverified_can_still_be_turned_off(monkeypatch):
    monkeypatch.setenv(C.SKILLS_UNVERIFIED_ENV, "0")
    assert C.skills_unverified() is False


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
        assert F.LIVE_SWITCHES[env] == "codeverse3d/skills/config.py"
    assert F.dead_env_keys({C.SKILLS_ENV: "on"}) == []


def test_doctor_reports_the_default_and_the_gemini_pin(monkeypatch):
    """`3dcode doctor --skills`: ON is the healthy state now, and gemini-cli's skills.enabled
    must be pinned in the per-session system settings (a user file could turn it off)."""
    from codeverse3d.doctor import check_skills

    monkeypatch.delenv(C.SKILLS_ENV, raising=False)
    rows = {name: status for name, status, _ in check_skills()}
    assert rows["skills switch"] == "OK" and rows["gemini-cli skills setting"] == "OK"
    assert rows["claude-code Skill tool"] == "OK"
    monkeypatch.setenv(C.SKILLS_ENV, "0")
    assert {name: status for name, status, _ in check_skills()}["skills switch"] == "WARN"
