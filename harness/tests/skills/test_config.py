"""The skill switches are ``Settings`` fields: ON by default, tolerant of garbage, live (never a dead A/B switch)."""

from __future__ import annotations

import pytest

from codeverse3d.config import Settings, get_settings


def _settings(monkeypatch, **env: str) -> Settings:
    for name in ("C3D_SKILLS", "C3D_SKILLS_MAX", "C3D_SKILLS_UNVERIFIED"):
        monkeypatch.delenv(name, raising=False)
    for name, raw in env.items():
        monkeypatch.setenv(name, raw)
    return Settings()


@pytest.mark.parametrize("name, raw, want", [
    ("C3D_SKILLS", "off", (False, True, 5)), ("C3D_SKILLS", "", (True, True, 5)), ("C3D_SKILLS", "maybe", (True, True, 5)),
    ("C3D_SKILLS_MAX", "3", (True, True, 3)), ("C3D_SKILLS_MAX", "-1", (True, True, 5)), ("C3D_SKILLS_MAX", "nope", (True, True, 5)),
])
def test_on_by_default_and_garbage_keeps_the_default(monkeypatch, name, raw, want):
    """Default ON (owner 2026-09-22); garbage warns and keeps the default, never a crash (D44 c)."""
    s = _settings(monkeypatch, **{name: raw})
    assert (s.skills, s.skills_unverified, s.skills_max) == want


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
