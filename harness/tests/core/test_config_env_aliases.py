"""One switch grammar, the flat spellings, and layered configuration."""

from __future__ import annotations

import logging

import pytest

from codeverse3d.config import Settings

#: every switch shares one validator: one top-level and one nested (flat-spelled) case
_FLAGS = [("C3D_SKILLS", True), ("C3D_SEED_RECIPES", True)]


@pytest.mark.parametrize(("env", "default"), _FLAGS)
def test_every_switch_speaks_one_grammar(monkeypatch, caplog, env, default):
    """on/off, 1/0, true/false, yes/no in any case and padding; empty is unset; else warn (D44 c)."""
    def read(raw: str | None) -> bool:
        if raw is None:
            monkeypatch.delenv(env, raising=False)
        else:
            monkeypatch.setenv(env, raw)
        s = Settings()
        return s.limits.seed_recipes if env == "C3D_SEED_RECIPES" else getattr(s, env[4:].lower())

    assert read(None) is default and read("") is default
    for word in ("off", "0", "false", "no", "OFF", " Off "):
        assert read(word) is False, word
    for word in ("on", "1", "true", "yes", "ON"):
        assert read(word) is True, word
    with caplog.at_level(logging.WARNING, logger="codeverse3d.config"):
        assert read("maybe") is default
    assert env in caplog.text


def test_flat_and_nested_max_in_flight_aliases(monkeypatch):
    monkeypatch.delenv("C3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("C3D_MAX_IN_FLIGHT", "16")
    assert Settings().rate.max_in_flight == 16
    monkeypatch.delenv("C3D_MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("C3D_RATE__MAX_IN_FLIGHT", "24")
    assert Settings().rate.max_in_flight == 24
    monkeypatch.setenv("C3D_MAX_IN_FLIGHT", "8")
    assert Settings().rate.max_in_flight == 8
    for name in ("C3D_MAX_IN_FLIGHT", "C3D_RATE__MAX_IN_FLIGHT"):   # both spellings reject a negative cap
        monkeypatch.delenv("C3D_MAX_IN_FLIGHT", raising=False)
        monkeypatch.delenv("C3D_RATE__MAX_IN_FLIGHT", raising=False)
        monkeypatch.setenv(name, "-5")
        with pytest.raises(ValueError, match="max_in_flight"):
            Settings()


def test_empty_is_unset_and_garbage_in_a_sizing_knob_is_loud(monkeypatch):
    """A cap is not a switch: an unparsable one stops the run before it starts."""
    monkeypatch.delenv("C3D_RATE__MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("C3D_MAX_IN_FLIGHT", "")
    assert Settings().rate.max_in_flight == 64
    monkeypatch.setenv("C3D_MAX_IN_FLIGHT", "sixteen")
    with pytest.raises(ValueError, match="max_in_flight"):
        Settings()


def test_render_gpu_is_read_by_both_spellings_and_garbage_is_loud(monkeypatch):
    """C3D_RENDER_GPU is the documented spelling; C3D_RENDER__GPU is the nested one."""
    monkeypatch.delenv("C3D_RENDER__GPU", raising=False)
    monkeypatch.setenv("C3D_RENDER_GPU", "off")
    assert Settings().render.gpu == "off"
    monkeypatch.setenv("C3D_RENDER_GPU", "ON")  # case-tolerant
    assert Settings().render.gpu == "on"
    monkeypatch.delenv("C3D_RENDER_GPU", raising=False)
    monkeypatch.setenv("C3D_RENDER__GPU", "off")
    assert Settings().render.gpu == "off"
    monkeypatch.setenv("C3D_RENDER_GPU", "maybe")
    with pytest.raises(ValueError, match="gpu"):
        Settings()


def test_the_rate_and_limits_dials_carry_their_bounds(monkeypatch):
    from codeverse3d.config import Limits, Rate

    for kwargs in ({"max_in_flight": -1}, {"hedge": 0}):
        with pytest.raises(ValueError):
            Rate(**kwargs)
    for kwargs in ({"max_parallel_agents": 0}, {"max_parallel_builds": -2}):
        with pytest.raises(ValueError):
            Limits(**kwargs)
    assert Rate(max_in_flight=0).max_in_flight == 0, "0 stays legal: it means unlimited"
    assert Limits(agent_max_turns=-1).agent_max_turns == 0, "a switch: a bad cap warns and asks for none"


# --------------------------------------------------------------------------- yaml layering
def test_a_project_file_overrides_only_the_keys_it_names(tmp_path, monkeypatch):
    """Project YAML overlays named keys without discarding user-config siblings."""
    import os

    from codeverse3d import config as C

    home = tmp_path / "fakehome"
    (home / ".config" / "3dcodeverse").mkdir(parents=True)
    (home / ".config" / "3dcodeverse" / "config.yaml").write_text(
        "limits:\n  agent_timeout_s: 900\n  max_parallel_builds: 3\njudge:\n  samples: 4\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "3dcodeverse.yaml").write_text("limits:\n  max_parallel_agents: 4\n")

    monkeypatch.setattr(C, "_USER_CONFIG", home / ".config" / "3dcodeverse" / "config.yaml")
    monkeypatch.setattr(C, "_LEGACY_USER_CONFIG", home / ".config" / "codeverse" / "config.yaml")
    monkeypatch.chdir(proj)
    for var in ("C3D_LIMITS__MAX_PARALLEL_AGENTS", "C3D_LIMITS__AGENT_TIMEOUT_S", "C3D_LIMITS__MAX_PARALLEL_BUILDS"):
        monkeypatch.delenv(var, raising=False)
    C.get_settings.cache_clear()
    try:
        s = C.get_settings()
        # the project file's own key applies ...
        assert s.limits.max_parallel_agents == 4
        # ... and the user's siblings in the SAME section survive it
        assert s.limits.agent_timeout_s == 900, "a sibling the project file never named must not reset"
        assert s.limits.max_parallel_builds == 3
        # a section the project file does not name is untouched (this always worked)
        assert s.judge.samples == 4
    finally:
        C.get_settings.cache_clear()
        os.environ.pop("C3D_PROFILE", None)


def test_the_environment_beats_the_config_files_per_setting(monkeypatch):
    """INSTALL §8.3: config files < C3D_* env, per setting — it decides A/B arms."""
    for name, raw in (("C3D_SKILLS", "0"), ("C3D_RUNS_DIR", "/from_env"), ("C3D_JUDGE__SAMPLES", "3")):
        monkeypatch.setenv(name, raw)
    s = Settings(skills=True, runs_dir="/from_yaml", judge={"samples": 2, "max_px": 512})
    assert (s.skills, str(s.runs_dir), s.judge.samples) == (False, "/from_env", 3)
    assert s.judge.max_px == 512, "a yaml sibling the environment does not name survives"


def test_runtime_js_dir_override_and_loud_failure(tmp_path, monkeypatch):
    """C3D_RUNTIME_JS supports relocation and rejects a missing directory."""

    good = tmp_path / "runtime_js"
    good.mkdir()
    monkeypatch.setenv("C3D_RUNTIME_JS", str(good))
    assert Settings().runtime_js_dir() == good
    monkeypatch.setenv("C3D_RUNTIME_JS", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="C3D_RUNTIME_JS"):
        Settings().runtime_js_dir()
    monkeypatch.delenv("C3D_RUNTIME_JS")
    assert Settings().runtime_js_dir().name == "runtime_js"   # editable checkout resolves


def test_the_config_names_before_d78_are_still_read_under_the_new_ones(tmp_path, monkeypatch):
    """The pre-D78 config file names are still read; a new file wins per setting."""
    from codeverse3d import config as C

    home = tmp_path / "fakehome"
    (home / ".config" / "codeverse").mkdir(parents=True)
    (home / ".config" / "codeverse" / "config.yaml").write_text("limits:\n  agent_timeout_s: 900\n  max_parallel_builds: 3\n")
    (home / ".config" / "3dcodeverse").mkdir(parents=True)
    (home / ".config" / "3dcodeverse" / "config.yaml").write_text("limits:\n  max_parallel_builds: 5\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "codeverse.yaml").write_text("rate:\n  max_in_flight: 8\n")
    monkeypatch.setattr(C, "_USER_CONFIG", home / ".config" / "3dcodeverse" / "config.yaml")
    monkeypatch.setattr(C, "_LEGACY_USER_CONFIG", home / ".config" / "codeverse" / "config.yaml")
    monkeypatch.chdir(proj)
    for var in ("C3D_MAX_IN_FLIGHT", "C3D_RATE__MAX_IN_FLIGHT", "C3D_LIMITS__AGENT_TIMEOUT_S", "C3D_LIMITS__MAX_PARALLEL_BUILDS"):
        monkeypatch.delenv(var, raising=False)
    C.get_settings.cache_clear()
    try:
        s = C.get_settings()
        assert (s.limits.agent_timeout_s, s.limits.max_parallel_builds, s.rate.max_in_flight) == (900, 5, 8)
    finally:
        C.get_settings.cache_clear()

