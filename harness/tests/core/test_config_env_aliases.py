"""Flat environment aliases and layered configuration."""

from __future__ import annotations

import pytest

from codeverse.config import Settings


def test_flat_and_nested_max_in_flight_aliases(monkeypatch):
    monkeypatch.delenv("CV3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "16")
    assert Settings().rate.max_in_flight == 16
    monkeypatch.delenv("CV3D_MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_RATE__MAX_IN_FLIGHT", "24")
    assert Settings().rate.max_in_flight == 24
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "8")
    assert Settings().rate.max_in_flight == 8


def test_empty_alias_is_ignored_and_garbage_is_loud(monkeypatch):
    monkeypatch.delenv("CV3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "")
    assert Settings().rate.max_in_flight == 64
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "sixteen")
    with pytest.raises(ValueError, match="CV3D_MAX_IN_FLIGHT"):
        Settings()


def test_render_gpu_is_read_by_both_spellings_and_garbage_is_loud(monkeypatch):
    """CV3D_RENDER_GPU is the spelling every doc and the node side use — it used to be
    read by NOTHING in Settings (only CV3D_RENDER__GPU was), so the documented knob
    was dead on 2 of 3 tracks."""
    monkeypatch.delenv("CV3D_RENDER__GPU", raising=False)
    monkeypatch.setenv("CV3D_RENDER_GPU", "off")
    assert Settings().render.gpu == "off"
    monkeypatch.setenv("CV3D_RENDER_GPU", "ON")  # case-tolerant
    assert Settings().render.gpu == "on"
    monkeypatch.delenv("CV3D_RENDER_GPU", raising=False)
    monkeypatch.setenv("CV3D_RENDER__GPU", "off")
    assert Settings().render.gpu == "off"
    monkeypatch.setenv("CV3D_RENDER_GPU", "maybe")
    with pytest.raises(ValueError, match="CV3D_RENDER_GPU"):
        Settings()


def test_a_negative_cap_is_rejected_by_both_spellings(monkeypatch):
    """Both spellings reject negative caps; zero remains the unlimited sentinel."""
    monkeypatch.delenv("CV3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("CV3D_MAX_IN_FLIGHT", "-5")
    with pytest.raises(ValueError, match="CV3D_MAX_IN_FLIGHT"):  # names the variable they typed
        Settings()
    monkeypatch.delenv("CV3D_MAX_IN_FLIGHT")
    monkeypatch.setenv("CV3D_RATE__MAX_IN_FLIGHT", "-5")
    with pytest.raises(ValueError, match="max_in_flight"):
        Settings()


def test_the_rate_and_limits_dials_carry_their_bounds(monkeypatch):
    """Reject rate/worker values that cannot make progress."""
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
    """Project YAML overlays named keys without discarding user-config siblings."""
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


def test_runtime_js_dir_override_and_loud_failure(tmp_path, monkeypatch):
    """CV3D_RUNTIME_JS supports relocation and rejects a missing directory."""

    good = tmp_path / "runtime_js"
    good.mkdir()
    monkeypatch.setenv("CV3D_RUNTIME_JS", str(good))
    assert Settings().runtime_js_dir() == good
    monkeypatch.setenv("CV3D_RUNTIME_JS", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="CV3D_RUNTIME_JS"):
        Settings().runtime_js_dir()
    monkeypatch.delenv("CV3D_RUNTIME_JS")
    assert Settings().runtime_js_dir().name == "runtime_js"   # editable checkout resolves
