"""The economy / balanced / quality dial: one name, the whole cost/quality setting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse.cli.main import app
from codeverse.config import Settings, get_settings
from codeverse.cost.profiles import PROFILE_NAMES, PROFILES, get_profile, profile_table

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_settings():
    """A profile mutates the process-wide settings singleton; never leak that."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_three_profiles_are_a_monotone_dial():
    usd = [PROFILES[n].expected_usd for n in PROFILE_NAMES]
    assert usd == sorted(usd) and usd[0] < usd[-1]
    rounds = [PROFILES[n].rounds for n in PROFILE_NAMES]
    assert rounds == sorted(rounds)
    # the judge payload is NOT part of the dial: two independent draws of the
    # 2-crop vs 1-crop experiment disagreed by 0.048 = 1.6x the pro judge's σ,
    # so no profile trades a fifth of a cent for that (docs/COST.md §14)
    assert {PROFILES[n].judge_detail_crops for n in PROFILE_NAMES} == {2}
    assert {PROFILES[n].judge_max_px for n in PROFILE_NAMES} == {1024}
    assert PROFILES["quality"].candidates == 2 and PROFILES["quality"].texture is True
    assert PROFILES["economy"].texture is False and PROFILES["economy"].candidates == 1
    # every profile explains itself and cites what it was measured at
    for p in PROFILES.values():
        assert p.expected_score and p.note and p.generator and p.judge


def test_balanced_is_todays_defaults():
    p = PROFILES["balanced"]
    s = Settings()
    assert (p.generator, p.planner, p.judge) == (s.default_generator, s.default_planner, s.default_judge)
    assert p.rounds == 4 and p.candidates == s.default_candidates and p.judge_samples == 1
    assert p.max_usd == 5.0 and p.max_minutes == 60.0 and p.judge_max_px == 1024


def test_economy_is_single_shot_flash_and_two_rounds_with_no_turn_cap_of_its_own():
    p = PROFILES["economy"]
    assert p.generator.startswith("single-shot:") and "flash" in p.judge
    assert p.rounds == 2 and p.judge_samples == 2
    # a single-shot generator opens no agent session, so economy's old 20-turn cap
    # could never fire — and the cap it was modelled on lost its own A/B (+$0.02,
    # −0.205 score, docs/COST.md §17), so no profile sets one
    assert p.max_turns == 0 and all(PROFILES[n].max_turns == 0 for n in PROFILE_NAMES)


def test_apply_profile_sets_the_whole_dial():
    s = Settings()
    p = s.apply_profile("quality")
    assert p.name == "quality" and s.profile == "quality"
    assert s.default_judge == p.judge and s.default_candidates == 2
    assert s.judge.samples == 3 and s.judge.max_px == 1024
    s2 = Settings()
    s2.apply_profile("economy")
    assert s2.judge.samples == 2 and s2.limits.agent_max_turns == 0
    # payload untouched: 768 px bills the same on Gemini and is noisier, and the
    # crop cut did not survive a second draw (docs/COST.md §14)
    assert s2.judge.detail_crops == 2 and s2.judge.max_px == 1024


def test_a_value_the_user_configured_survives_the_profile_unless_forced():
    s = Settings(default_generator="codex:gpt-5.6-sol")
    s.apply_profile("economy")
    assert s.default_generator == "codex:gpt-5.6-sol"  # config.yaml / CV3D_* wins over a default dial
    assert s.default_judge == "gemini:gemini-3.7-flash"  # everything unstated still moves
    s.apply_profile("economy", force=True)
    assert s.default_generator == "single-shot:gemini:gemini-3.7-flash"


def test_unknown_profile_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown profile"):
        get_profile("cheapest")


def test_profile_table_and_cli():
    rows = profile_table()
    assert len(rows) == 3 and rows[0][0] == "economy"
    r = runner.invoke(app, ["cost", "profiles"])
    assert r.exit_code == 0 and "economy" in r.stdout and "quality" in r.stdout


def test_make_profile_writes_the_whole_shape_onto_the_spec(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run", "--profile", "quality"])
    assert r.exit_code == 0, r.output
    spec = json.loads(next(runs.iterdir()).joinpath("spec.json").read_text())
    assert spec["options"] == {"candidates": 2, "texture": True, "profile": "quality"}
    assert spec["budget"]["max_rounds"] == 4 and spec["budget"]["max_usd"] == 8.0
    assert spec["backends"]["judge"] == "gemini:gemini-3.1-pro-preview"


def test_an_explicit_flag_beats_the_profile(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run",
                            "--profile", "economy", "--rounds", "4", "--max-usd", "9",
                            "--generator", "api-agent:gemini:gemini-3.7-flash"])
    assert r.exit_code == 0, r.output
    spec = json.loads(next(runs.iterdir()).joinpath("spec.json").read_text())
    assert spec["budget"]["max_rounds"] == 4 and spec["budget"]["max_usd"] == 9.0
    assert spec["backends"]["generator"] == "api-agent:gemini:gemini-3.7-flash"
    assert spec["backends"]["judge"] == "gemini:gemini-3.7-flash"  # unstated → still the profile's


def test_no_profile_flag_leaves_the_defaults_alone(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run"])
    assert r.exit_code == 0, r.output
    spec = json.loads(next(runs.iterdir()).joinpath("spec.json").read_text())
    # the resolved dial is recorded whichever way it was named, so `3dcv resume`
    # reproduces it; with no flag and no env that dial is the default, balanced
    assert spec["options"]["profile"] == "balanced" and spec["options"]["texture"] is False
    assert spec["budget"]["max_rounds"] == 4 and spec["budget"]["max_usd"] == 5.0


def test_judge_samples_reach_the_round_policy_only_when_a_profile_asks(tmp_path: Path):
    from codeverse.cli import _common as C
    from codeverse.contracts.common import Budget, Language, Track
    from codeverse.contracts.spec import Spec

    spec = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="p",
                budget=Budget(max_rounds=3))
    s = Settings()
    assert C.round_policy_options(spec, s) == {}  # n=1: the track keeps its own policy
    s.apply_profile("quality", force=True)
    opts = C.round_policy_options(spec, s)
    assert opts["policy"].judge_samples == 3 and opts["policy"].max_rounds == 3
    # the rubric threshold is bound here because injecting a policy skips BaseTrack's own binding
    assert opts["policy"].target == pytest.approx(0.72)


# --------------------------------------------------------------- flag == env var
def _dial_from_flag(name: str):
    """The dial `3dcv make --profile <name>` resolves to."""
    from codeverse.cli import _common as C

    return C.resolve_dial(Settings(), name)


def _dial_from_env(name: str, monkeypatch):
    """The dial `CV3D_PROFILE=<name> 3dcv make` resolves to (the real settings path)."""
    from codeverse.cli import _common as C

    monkeypatch.setenv("CV3D_PROFILE", name)
    get_settings.cache_clear()
    try:
        return C.resolve_dial(get_settings(), None)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_the_flag_and_the_env_var_resolve_to_the_same_dial(name, monkeypatch):
    """`--profile X` and `CV3D_PROFILE=X` must set the WHOLE dial, not two thirds
    of it: the verifier found the env path skipped candidates and the texture pass
    because the CLI read those off the flag instead of the resolved profile."""
    assert _dial_from_flag(name) == _dial_from_env(name, monkeypatch)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("economy", {"generator": "single-shot:gemini:gemini-3.7-flash",
                     "judge": "gemini:gemini-3.7-flash", "judge_samples": 2,
                     "rounds": 2, "candidates": 1, "texture": False,
                     "judge_max_px": 1024, "judge_montages": 3, "judge_detail_crops": 2,
                     "agent_max_turns": 0, "max_usd": 1.50, "max_minutes": 30.0}),
        ("balanced", {"generator": "api-agent:gemini:gemini-3.7-flash",
                      "judge": "gemini:gemini-3.1-pro-preview", "judge_samples": 1,
                      "rounds": 4, "candidates": 1, "texture": False,
                      "judge_max_px": 1024, "judge_montages": 3, "judge_detail_crops": 2,
                      "agent_max_turns": 0, "max_usd": 5.0, "max_minutes": 60.0}),
        ("quality", {"generator": "api-agent:gemini:gemini-3.7-flash",
                     "judge": "gemini:gemini-3.1-pro-preview", "judge_samples": 3,
                     "rounds": 4, "candidates": 2, "texture": True,
                     "judge_max_px": 1024, "judge_montages": 3, "judge_detail_crops": 2,
                     "agent_max_turns": 0, "max_usd": 8.0, "max_minutes": 90.0}),
    ],
)
def test_each_profile_resolves_to_its_documented_dial(name, expected, monkeypatch):
    """Every dial value docs/COST.md §15 promises, from BOTH entry points."""
    for dial in (_dial_from_flag(name), _dial_from_env(name, monkeypatch)):
        assert dial.profile == name
        for field, want in expected.items():
            assert getattr(dial, field) == want, f"{name}.{field}"


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_the_env_var_reaches_the_spec_a_make_writes(name, tmp_path: Path, monkeypatch):
    """End to end: CV3D_PROFILE alone must produce the same spec shape as --profile."""
    from codeverse.cost.profiles import PROFILES

    p = PROFILES[name]
    monkeypatch.setenv("CV3D_PROFILE", name)
    get_settings.cache_clear()
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run"])
    assert r.exit_code == 0, r.output
    spec = json.loads(next(runs.iterdir()).joinpath("spec.json").read_text())
    assert spec["options"] == {"candidates": p.candidates, "texture": p.texture, "profile": name}
    assert spec["budget"]["max_rounds"] == p.rounds and spec["budget"]["max_usd"] == p.max_usd
    assert spec["backends"]["judge"] == p.judge and spec["backends"]["generator"] == p.generator
