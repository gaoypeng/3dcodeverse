"""``tracks/plan_features.py``: the switch registry that keeps A/B rigs honest about
which env switches any code actually reads (CQ-5)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from codeverse3d.tracks import plan_features as F

# --------------------------------------------------------------------------- CQ-5
HARNESS = Path(__file__).resolve().parents[2]


@cache
def _sources() -> tuple[tuple[str, str], ...]:
    """(relpath, text) for every harness source, read ONCE per session.

    Only ``codeverse3d`` counts: a switch is live when the HARNESS reads it.  The evaluation
    scripts (``eval/bench``) set switches for an A/B; they are not what makes one live.
    """
    out = []
    for p in (HARNESS / "codeverse3d").rglob("*.py"):
        if p.name != "plan_features.py":
            out.append((str(p.relative_to(HARNESS)), p.read_text(errors="replace")))
    return tuple(out)


def _readers(name: str) -> list[str]:
    return [rel for rel, text in _sources() if name in text]


@pytest.mark.parametrize("name", sorted(F.LIVE_SWITCHES))
def test_every_live_switch_is_really_read(name: str):
    """A name in LIVE_SWITCHES that nothing reads is the CQ-5 bug all over again."""
    readers = _readers(name)
    assert readers, f"{name} is listed as live but no module reads it"
    assert F.LIVE_SWITCHES[name] in readers, f"{name} is read by {readers}, not {F.LIVE_SWITCHES[name]}"


@pytest.mark.parametrize("name", sorted(F.DEAD_SWITCHES))
def test_every_dead_switch_is_really_dead(name: str):
    """The mirror guard: once something reads the variable, it must leave DEAD_SWITCHES —
    otherwise ab_plan would refuse a legitimate A/B."""
    assert not _readers(name), f"{name} is now read by {_readers(name)}; move it to LIVE_SWITCHES"


def test_the_plan_features_switch_is_declared_dead():
    """It is: the six KNOWN_FEATURES are not implemented anywhere, so an arm that differs
    only by C3D_PLAN_FEATURES is byte-identical to its control."""
    assert F.PLAN_FEATURES_ENV in F.DEAD_SWITCHES
    assert F.dead_env_keys({F.PLAN_FEATURES_ENV: "all"}) == [F.PLAN_FEATURES_ENV]
    assert F.dead_env_keys({"C3D_PLAN_BRIEF": "off"}) == []
    assert F.dead_env_keys({F.PLAN_FEATURES_ENV: "all", "C3D_PLAN_BRIEF": "off"}) == [F.PLAN_FEATURES_ENV]


# --------------------------------------------------------------------- pin-plan safety
def test_a_generation_side_switch_may_share_one_plan():
    """`contacts` renders a table into the BUILDER's prompt from an unchanged plan, so
    both arms can be seeded with the same plan.json and the paired delta stops carrying
    the planner's spread — the dominant variance term (docs/EVAL.md §8.1)."""
    from codeverse3d.tracks.plan_features import pin_plan_blockers

    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "contacts"}) == []
    for name in sorted(F.GENERATION_SIDE_ENV):
        assert pin_plan_blockers({name: "1"}) == [], name
    assert pin_plan_blockers({}) == []


def test_a_plan_side_switch_is_refused_by_name():
    """Pinning `fit` would hand both arms one plan and so silently delete the change
    under test — the rig would then report "no effect" with confidence."""
    from codeverse3d.tracks.plan_features import pin_plan_blockers

    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "fit"}) == [
        "C3D_PLAN_FEATURES=fit changes the plan itself"]
    # one plan-side name in a list of otherwise-safe ones still blocks
    assert pin_plan_blockers({"C3D_PLAN_FEATURES": "contacts,fit"}) == [
        "C3D_PLAN_FEATURES=fit changes the plan itself"]
    assert len(pin_plan_blockers({"C3D_PLAN_FEATURES": "all"})) == 5


def test_an_unclassified_switch_defaults_to_refusing():
    """Refusing to pin costs one noisy A/B; pinning wrongly costs a confident wrong
    answer.  So the default for anything unknown is: do not pin."""
    from codeverse3d.tracks.plan_features import pin_plan_blockers

    assert pin_plan_blockers({"C3D_MYSTERY_KNOB": "1"}) == [
        "C3D_MYSTERY_KNOB is not known to act after planning"]
    assert pin_plan_blockers({"C3D_PLAN_BRIEF": "off"}) == [
        "C3D_PLAN_BRIEF is not known to act after planning"]


def test_every_known_feature_is_classified():
    """A new feature must be put on one side or the other in the same commit; otherwise
    it silently inherits 'plan-side' and nobody notices the A/B got noisier."""
    from codeverse3d.tracks.plan_features import GENERATION_SIDE, KNOWN_FEATURES

    assert set(KNOWN_FEATURES) > GENERATION_SIDE
