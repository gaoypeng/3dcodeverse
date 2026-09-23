"""The skill switches are ``Settings`` fields: ON by default, tolerant of garbage, live (never a dead A/B switch)."""

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


@pytest.mark.parametrize("raw, want", [("off", False), ("", True), ("maybe", True)])
def test_on_by_default_and_garbage_keeps_the_default(monkeypatch, raw, want):
    """Default ON (D-rule, owner 2026-09-22); garbage warns and keeps it, never a crash (D44 c)."""
    s = _settings(monkeypatch, C3D_SKILLS=raw)
    assert (s.skills, s.skills_unverified, s.skills_max) == (want, True, 5)


@pytest.mark.parametrize("raw, want", [("3", 3), ("-1", 5), ("nope", 5)])
def test_max_is_never_a_crash(monkeypatch, raw, want):
    assert _settings(monkeypatch, C3D_SKILLS_MAX=raw).skills_max == want


def test_the_switches_are_live():
    assert dead_env_keys(dict.fromkeys(("C3D_SKILLS", "C3D_SKILLS_MAX", "C3D_SKILLS_UNVERIFIED",
                                        "C3D_SKILLS_ONLY", "C3D_SKILLS_DIR"), "1")) == []


def test_doctor_reports_the_default_and_the_gemini_pin(monkeypatch):
    """ON is healthy, and gemini-cli's skills.enabled must be pinned per session."""
    from codeverse3d.doctor import check_skills

    monkeypatch.delenv("C3D_SKILLS", raising=False)
    rows = {name: status for name, status, _ in check_skills()}
    assert rows["skills switch"] == "OK" and rows["gemini-cli skills setting"] == "OK"
    assert rows["claude-code Skill tool"] == "OK"
    monkeypatch.setenv("C3D_SKILLS", "0")
    get_settings.cache_clear()
    assert {name: status for name, status, _ in check_skills()}["skills switch"] == "WARN"
