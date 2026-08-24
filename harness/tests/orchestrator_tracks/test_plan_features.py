"""The one env switch every plan-loop-engineering change reads (``tracks/plan_features.py``)."""

from __future__ import annotations

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
