"""The skill switches are ``Settings`` fields: ON by default, tolerant of garbage, live.

``tracks/plan_features.py`` exists because a wave A/B'd a switch nothing read and printed
"keep, mean delta +0.344" for two byte-identical arms.  These tests are what keep
``C3D_SKILLS`` from becoming that switch.
"""

from __future__ import annotations

import pytest

from codeverse3d.config import Settings, get_settings
from codeverse3d.tracks.plan_features import dead_env_keys


def _settings(monkeypatch, **env: str) -> Settings:
    for name in ("C3D_SKILLS", "C3D_SKILLS_MAX", "C3D_SKILLS_UNVERIFIED"):
        monkeypatch.delenv(name, raising=False)
    for name, raw in env.items():
        monkeypatch.setenv(name, raw)
    return Settings()


def test_on_by_default(monkeypatch):
    """Flipped 2026-09-22 (owner): every backend is a vendor CLI with a native skill loader,
    and the whole library routes — the cap of 5 per session is the cost knob, not the switch."""
    s = _settings(monkeypatch)
    assert (s.skills, s.skills_unverified, s.skills_max) == (True, True, 5)


@pytest.mark.parametrize("raw, want", [("on", True), ("1", True), ("true", True), ("YES", True),
                                       ("off", False), ("0", False), ("no", False), ("", True),
                                       ("maybe", True)])
def test_flag_spellings(monkeypatch, raw, want):
    """Unset or empty is the default (ON); garbage warns and keeps the default — a typo in
    a bench command runs the arm that never set it, never a crash (D44 c)."""
    assert _settings(monkeypatch, C3D_SKILLS=raw).skills is want


def test_unverified_can_still_be_turned_off(monkeypatch):
    assert _settings(monkeypatch, C3D_SKILLS_UNVERIFIED="0").skills_unverified is False


@pytest.mark.parametrize("raw, want", [("3", 3), ("0", 0), ("-1", 5), ("nope", 5), ("", 5)])
def test_max_is_never_a_crash(monkeypatch, raw, want):
    assert _settings(monkeypatch, C3D_SKILLS_MAX=raw).skills_max == want


def test_the_switches_are_live():
    assert dead_env_keys(dict.fromkeys(("C3D_SKILLS", "C3D_SKILLS_MAX", "C3D_SKILLS_UNVERIFIED",
                                        "C3D_SKILLS_ONLY", "C3D_SKILLS_DIR"), "1")) == []


def test_doctor_reports_the_default_and_the_gemini_pin(monkeypatch):
    """`3dcode doctor --skills`: ON is the healthy state now, and gemini-cli's skills.enabled
    must be pinned in the per-session system settings (a user file could turn it off)."""
    from codeverse3d.doctor import check_skills

    monkeypatch.delenv("C3D_SKILLS", raising=False)
    rows = {name: status for name, status, _ in check_skills()}
    assert rows["skills switch"] == "OK" and rows["gemini-cli skills setting"] == "OK"
    assert rows["claude-code Skill tool"] == "OK"
    monkeypatch.setenv("C3D_SKILLS", "0")
    get_settings.cache_clear()
    assert {name: status for name, status, _ in check_skills()}["skills switch"] == "WARN"
