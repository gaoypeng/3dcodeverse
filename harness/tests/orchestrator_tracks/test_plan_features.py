"""``tracks/plan_features.py``: the plan-loop switch registry, and the guard that keeps
it honest about which switches any code actually reads (CQ-5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.tracks import plan_features as F


def test_empty_means_nothing_on(monkeypatch):
    monkeypatch.delenv(F.PLAN_FEATURES_ENV, raising=False)
    assert F.plan_features() == frozenset()
    assert not F.plan_feature_on(F.FIT)
    monkeypatch.setenv(F.PLAN_FEATURES_ENV, "   ")
    assert F.plan_features() == frozenset()


def test_parse_names_case_and_spaces():
    assert F.parse_features("fit, Contacts ,") == {F.FIT, F.CONTACTS}


def test_all_and_negation():
    assert F.parse_features("all") == frozenset(F.KNOWN_FEATURES)
    assert F.parse_features("all,-graph") == frozenset(F.KNOWN_FEATURES) - {F.GRAPH}
    # order matters: a later positive re-enables
    assert F.GRAPH in F.parse_features("-graph,graph")


def test_unknown_names_are_dropped_not_fatal(caplog):
    with caplog.at_level("WARNING"):
        assert F.parse_features("fit,typo") == {F.FIT}
    assert any("typo" in r.message for r in caplog.records)


def test_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv(F.PLAN_FEATURES_ENV, "fit")
    assert F.plan_feature_on(F.FIT) and not F.plan_feature_on(F.CONTACTS)
    monkeypatch.setenv(F.PLAN_FEATURES_ENV, "contacts")
    assert F.plan_feature_on(F.CONTACTS) and not F.plan_feature_on(F.FIT)


def test_asking_about_an_undefined_feature_is_a_bug():
    with pytest.raises(ValueError):
        F.plan_feature_on("not_a_feature")


# --------------------------------------------------------------------------- CQ-5
HARNESS = Path(__file__).resolve().parents[2]


def _sources() -> list[Path]:
    return [p for d in ("codeverse", "bench") for p in (HARNESS / d).rglob("*.py")
            if p.name != "plan_features.py"]


def _readers(name: str) -> list[str]:
    return [str(p.relative_to(HARNESS)) for p in _sources() if name in p.read_text(errors="replace")]


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
    only by CV3D_PLAN_FEATURES is byte-identical to its control."""
    assert F.PLAN_FEATURES_ENV in F.DEAD_SWITCHES
    assert F.dead_env_keys({F.PLAN_FEATURES_ENV: "all"}) == [F.PLAN_FEATURES_ENV]
    assert F.dead_env_keys({"CV3D_PLAN_BRIEF": "off"}) == []
    assert F.dead_env_keys({F.PLAN_FEATURES_ENV: "all", "CV3D_PLAN_BRIEF": "off"}) == [F.PLAN_FEATURES_ENV]


def test_ab_plan_refuses_an_ab_whose_only_switch_is_dead(capsys):
    """`--variant-env CV3D_PLAN_FEATURES=all` produced a full battery and the verdict
    'keep, mean delta +0.344' for two byte-identical arms.  It must not start."""
    import bench.ab_plan as A

    with pytest.raises(SystemExit):
        A.main(["--prompts", "p.yaml", "--out", "o", "--variant-env", "CV3D_PLAN_FEATURES=all"])
    err = capsys.readouterr().err
    assert "nothing reads" in err and "CV3D_PLAN_FEATURES" in err


def test_ab_plan_still_accepts_a_live_switch(monkeypatch, capsys):
    """The guard must not block a real A/B: it fires only when EVERY key is dead.

    Offline — --no-preflight and --allow-siblings keep the provider health check and the
    docs/COST.md §23 admission check out of it, and run_ab itself is stubbed."""
    import bench.ab_plan as A

    seen: list[dict[str, str]] = []
    class _V:
        decision, reason, caution = "keep", "stubbed", ""

    monkeypatch.setattr(A, "run_ab", lambda battery, out, opts, **kw: (seen.append(opts.variant_env), _V())[1])
    for argv in (["--variant-env", "CV3D_PLAN_BRIEF=off"],
                 ["--variant-env", "CV3D_PLAN_FEATURES=all", "--variant-env", "CV3D_PLAN_BRIEF=off"]):
        A.main(["--prompts", "p.yaml", "--out", "o", "--no-preflight", "--allow-siblings", *argv])

    assert len(seen) == 2, capsys.readouterr()
    assert "CV3D_PLAN_BRIEF" in seen[0]
