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


def test_a_negative_cap_is_rejected_by_both_spellings(monkeypatch):
    """SM-07: ``CV3D_MAX_IN_FLIGHT=-5`` used to survive Settings, be reported as *fitting*
    the pool budget by ``3dcv doctor`` (-5 <= headroom), poison the shared in-flight
    accounting every sibling process reads, and finally die as a bare
    'ValueError: semaphore initial value must be >= 0' from threading.BoundedSemaphore
    inside KeyPool — at the first model call, after the workspace and spec.json were on
    disk.  0 means unlimited here, so -1 / -5 is exactly what an operator reaches for."""
    monkeypatch.delenv("CV3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "-5")
    with pytest.raises(ValueError, match="CV3D_MAX_IN_FLIGHT"):  # names the variable they typed
        Settings()
    monkeypatch.delenv("CV3D_MAX_IN_FLIGHT")
    monkeypatch.setenv("CV3D_RATE__MAX_IN_FLIGHT", "-5")
    with pytest.raises(ValueError, match="max_in_flight"):
        Settings()


def test_the_rate_and_limits_dials_carry_their_bounds(monkeypatch):
    """The same class of value elsewhere: a 0 RPM bucket never refills and a 0-worker
    fan-out cannot start a thread, so both are rejected at construction, not at use."""
    from codeverse.config import Limits, Rate

    for kwargs in ({"max_in_flight": -1}, {"rpm_per_key": 0}, {"tpm_per_key": -1}):
        with pytest.raises(ValueError):
            Rate(**kwargs)
    for kwargs in ({"max_parallel_agents": 0}, {"max_parallel_builds": -2}, {"agent_max_turns": -1}):
        with pytest.raises(ValueError):
            Limits(**kwargs)
    assert Rate(max_in_flight=0).max_in_flight == 0, "0 stays legal: it means unlimited"


# --------------------------------------------------------------------------- yaml layering
def test_a_project_file_overrides_only_the_keys_it_names(tmp_path, monkeypatch):
    """SM-04: the two YAML files were merged with ``dict.update``, so naming a section in
    ./codeverse.yaml replaced the WHOLE sub-dict and every sibling key the user set in
    ~/.config/codeverse/config.yaml fell back to the built-in Field default — not to the
    user's value.  Here that means the key pool scheduling against the built-in
    1,000,000 TPM when the operator declared 250,000, 4x their real quota, merely because
    the project file mentions `rate:` at all.  docs/INSTALL.md §8.3 documents the order as
    "built-in defaults < user config < project config < env", which reads as per-setting."""
    import os

    from codeverse import config as C

    home = tmp_path / "fakehome"
    (home / ".config" / "codeverse").mkdir(parents=True)
    (home / ".config" / "codeverse" / "config.yaml").write_text(
        "rate:\n  tpm_per_key: 250000\n  rpm_per_key: 300\njudge:\n  samples: 4\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "codeverse.yaml").write_text("rate:\n  max_in_flight: 8\n")

    monkeypatch.setattr(C, "_USER_CONFIG", home / ".config" / "codeverse" / "config.yaml")
    monkeypatch.chdir(proj)
    for var in ("CV3D_MAX_IN_FLIGHT", "CV3D_RATE__MAX_IN_FLIGHT", "CV3D_RATE__TPM_PER_KEY"):
        monkeypatch.delenv(var, raising=False)
    C.get_settings.cache_clear()
    try:
        s = C.get_settings()
        # the project file's own key applies ...
        assert s.rate.max_in_flight == 8
        # ... and the user's siblings in the SAME section survive it
        assert s.rate.tpm_per_key == 250000, "the binding TPM limit must not silently reset"
        assert s.rate.rpm_per_key == 300
        # a section the project file does not name is untouched (this always worked)
        assert s.judge.samples == 4
    finally:
        C.get_settings.cache_clear()
        os.environ.pop("CV3D_PROFILE", None)


def test_deep_merge_overlays_per_key_at_every_depth():
    from codeverse.config import _deep_merge

    base = {"rate": {"tpm": 1, "rpm": 2}, "judge": {"samples": 4}, "scalar": 1}
    over = {"rate": {"tpm": 9}, "scalar": 2, "new": {"a": 1}}
    assert _deep_merge(base, over) == {
        "rate": {"tpm": 9, "rpm": 2}, "judge": {"samples": 4}, "scalar": 2, "new": {"a": 1}}
    assert base == {"rate": {"tpm": 1, "rpm": 2}, "judge": {"samples": 4}, "scalar": 1}, "no mutation"
    # a non-dict overlay replaces a dict outright rather than trying to merge into it
    assert _deep_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}
