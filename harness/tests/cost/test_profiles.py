"""The economy / balanced / quality dial: one name, the whole cost/quality setting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse3d.cli._common import resolve_dial
from codeverse3d.cli.main import app
from codeverse3d.config import Settings, get_settings
from codeverse3d.cost.profiles import PROFILE_NAMES, profile_table

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_settings():
    """A profile mutates the process-wide settings singleton; never leak that."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_value_the_user_configured_survives_the_profile_unless_forced(monkeypatch):
    s = Settings(default_generator="codex:gpt-5.6-sol")
    s.apply_profile("economy")
    assert s.default_generator == "codex:gpt-5.6-sol"  # config.yaml / C3D_* wins over a default dial
    assert s.default_judge == "gemini:gemini-3.7-flash"  # everything unstated still moves
    s.apply_profile("economy", force=True)
    assert s.default_generator == "single-shot:gemini:gemini-3.7-flash"
    # a stated judge field freezes only itself, not the whole judge section
    monkeypatch.setenv("C3D_JUDGE__MAX_PX", "800")
    s = Settings()
    s.apply_profile("quality")
    assert s.judge.max_px == 800, "the field the user stated wins"
    assert s.judge.samples == 3, "every field the user did NOT state still follows the profile"
    assert s.judge.montages == 5 and s.judge.detail_crops == 2


def test_no_profile_touches_the_judge_payload_or_the_turn_cap(monkeypatch):
    """Not dials (docs/COST.md §14, §17): even a forced profile leaves them alone."""
    monkeypatch.setenv("C3D_JUDGE__MAX_PX", "800")
    monkeypatch.setenv("C3D_LIMITS__AGENT_MAX_TURNS", "12")
    for name in PROFILE_NAMES:
        d = resolve_dial(Settings(), name)
        assert (d.judge_max_px, d.judge_montages, d.judge_detail_crops, d.agent_max_turns) == (800, 5, 2, 12)


def test_a_bogus_profile_name_is_a_typed_cli_error_not_a_traceback(monkeypatch):
    """An invalid environment profile yields one clean validation error."""
    runner = CliRunner()
    monkeypatch.setenv("C3D_PROFILE", "bogus")
    get_settings.cache_clear()
    try:
        r = runner.invoke(app, ["doctor"])
        out = " ".join(r.output.split())  # rich wraps at the console width
        assert r.exit_code == 2 and r.exception.__class__.__name__ != "ValueError"
        assert "unknown profile 'bogus'" in out and "economy, balanced, quality" in out
        assert "Traceback" not in out
    finally:
        get_settings.cache_clear()
    # `make --profile bogus`: flywheel_cli/test_cli.py test_an_unknown_profile_is_a_clean_error_not_a_traceback


def test_profile_table_and_cli():
    rows = profile_table()
    assert len(rows) == 3 and rows[0][0] == "economy"
    r = runner.invoke(app, ["cost", "profiles"])
    assert r.exit_code == 0 and "economy" in r.stdout and "quality" in r.stdout


def test_an_explicit_flag_beats_the_profile(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run", "--slug", "pot",
                            "--profile", "economy", "--rounds", "4",
                            "--generator", "gemini-cli:gemini-3.6-flash"])
    assert r.exit_code == 0, r.output
    spec = json.loads((runs / "pot" / "spec.json").read_text())
    assert spec["budget"]["max_rounds"] == 4 and spec["budget"]["max_minutes"] > 0
    assert spec["backends"]["generator"] == "gemini-cli:gemini-3.6-flash"
    assert spec["backends"]["judge"] == "gemini:gemini-3.7-flash"  # unstated → still the profile's


def test_judge_samples_reach_the_round_policy_only_when_a_profile_asks(tmp_path: Path):
    from codeverse3d.cli import _common as C
    from codeverse3d.contracts.common import Budget, Language, Track
    from codeverse3d.contracts.spec import Spec

    spec = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="p",
                budget=Budget(max_rounds=3))
    s = Settings()
    assert C.round_policy_options(spec, s) == {}  # n=1: the track keeps its own policy
    s.apply_profile("quality", force=True)
    opts = C.round_policy_options(spec, s)
    assert opts["policy"].judge_samples == 3 and opts["policy"].max_rounds == 3


# --------------------------------------------------------------- flag == env var
def _dial_from_flag(name: str):
    """The dial `3dcode make --profile <name>` resolves to."""
    from codeverse3d.cli import _common as C

    return C.resolve_dial(Settings(), name)


def _dial_from_env(name: str, monkeypatch):
    """The dial `C3D_PROFILE=<name> 3dcode make` resolves to (the real settings path)."""
    from codeverse3d.cli import _common as C

    monkeypatch.setenv("C3D_PROFILE", name)
    get_settings.cache_clear()
    try:
        return C.resolve_dial(get_settings(), None)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("economy", {"generator": "single-shot:gemini:gemini-3.7-flash",
                     "judge": "gemini:gemini-3.7-flash", "judge_samples": 2,
                     "rounds": 2, "candidates": 1, "texture": False,
                     "judge_max_px": 1024, "judge_montages": 5, "judge_detail_crops": 2,
                     "agent_max_turns": 0, "max_minutes": 30.0}),
        ("balanced", {"generator": "gemini-cli:gemini-3.7-flash",
                      "judge": "gemini:gemini-3.1-pro-preview", "judge_samples": 1,
                      "rounds": 4, "candidates": 1, "texture": False,
                      "judge_max_px": 1024, "judge_montages": 5, "judge_detail_crops": 2,
                      "agent_max_turns": 0, "max_minutes": 60.0}),
        ("quality", {"generator": "gemini-cli:gemini-3.7-flash",
                     "judge": "gemini:gemini-3.1-pro-preview", "judge_samples": 3,
                     "rounds": 4, "candidates": 2, "texture": True,
                     "judge_max_px": 1024, "judge_montages": 5, "judge_detail_crops": 2,
                     "agent_max_turns": 0, "max_minutes": 90.0}),
    ],
)
def test_each_profile_resolves_to_its_documented_dial(name, expected, monkeypatch):
    """Every dial value docs/COST.md §15 promises, from BOTH entry points."""
    settings = Settings()
    applied = settings.apply_profile(name)
    assert applied.name == settings.profile == name
    for dial in (_dial_from_flag(name), _dial_from_env(name, monkeypatch)):
        assert dial.profile == name
        for field, want in expected.items():
            assert getattr(dial, field) == want, f"{name}.{field}"
