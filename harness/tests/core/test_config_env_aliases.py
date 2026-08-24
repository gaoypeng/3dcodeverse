"""Flat env aliases for nested settings (codeverse/config.py Settings._FLAT_ALIASES).

Regression (2026-08-24): ``CV3D_MAX_IN_FLIGHT=16`` was written into three battery launches
and two workflow briefs, and pydantic-settings only read ``CV3D_RATE__MAX_IN_FLIGHT`` — so
every one of them silently ran at the default 64 while docs/COST.md §23 was telling people
to set it.  An env knob that is read by nothing is worse than no knob.
"""

from __future__ import annotations

import pytest

from codeverse.config import Settings


def test_flat_alias_sets_the_nested_field(monkeypatch):
    monkeypatch.delenv("CV3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "16")
    assert Settings().rate.max_in_flight == 16


def test_nested_name_still_works(monkeypatch):
    monkeypatch.delenv("CV3D_MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_RATE__MAX_IN_FLIGHT", "24")
    assert Settings().rate.max_in_flight == 24


def test_flat_alias_wins_when_both_are_set(monkeypatch):
    """The short name is what the doctor prints and what people type; if both are present
    the one a person set on the command line should win."""
    monkeypatch.setenv("CV3D_RATE__MAX_IN_FLIGHT", "24")
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "8")
    assert Settings().rate.max_in_flight == 8


def test_empty_alias_is_ignored_and_garbage_is_loud(monkeypatch):
    monkeypatch.delenv("CV3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "")
    assert Settings().rate.max_in_flight == 64
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "sixteen")
    with pytest.raises(ValueError, match="CV3D_MAX_IN_FLIGHT"):
        Settings()


def test_doctor_prints_a_name_that_is_actually_read():
    """The `pool sharing` row tells people which env var to set; it must be one that works."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "codeverse" / "cli" / "doctor.py").read_text()
    assert "CV3D_MAX_IN_FLIGHT=" in src
    assert "CV3D_MAX_IN_FLIGHT" in Settings._FLAT_ALIASES
