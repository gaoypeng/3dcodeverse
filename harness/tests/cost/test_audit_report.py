"""The audit over a tree of runs and its report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.cost import audit_runs
from codeverse.cost.audit import (
    cached_input_share,
    price_confidence,
    stage_latency,
    uncached_if_no_cache,
)
from codeverse.cost.report import console, markdown


def test_audit_of_one_run(fake_run: Path):
    audit = audit_runs([fake_run.parent])
    assert audit.n_runs == 1 and audit.n_passed == 1
    assert audit.usd_per_passing_artifact == pytest.approx(audit.total_usd)
    assert audit.bucket("track", "static_object").cost_usd == pytest.approx(audit.total_usd)
    assert audit.calls_per_round() > 0
    cached, total, usd = cached_input_share(audit)
    assert 0 < cached < total and usd > 0
    assert uncached_if_no_cache(audit) > audit.total_usd
    assert sum(price_confidence(audit).values()) == pytest.approx(audit.total_usd)
    assert stage_latency(audit)


def test_waste_finds_a_regression(fake_run: Path, tmp_path: Path):
    """A refine round that scores below the best is money spent on a worse artifact."""
    import shutil

    ws = tmp_path / "regressed"
    shutil.copytree(fake_run, ws)
    record = json.loads((ws / "record.json").read_text())
    r0 = record["rounds"][0]
    r1 = json.loads(json.dumps(r0))
    r1["index"] = 1
    r1["kind"] = "refine"
    r1["judgment"]["overall"] = 0.5  # worse than r00's 0.8
    record["rounds"].append(r1)
    record["total_usage"]["cost_usd"] *= 2
    (ws / "record.json").write_text(json.dumps(record))
    audit = audit_runs([ws])
    kinds = audit.waste_by_kind()
    assert "regression" in kinds and kinds["regression"][1] > 0
    assert any("scored 0.500 vs 0.800" in w.detail for w in audit.waste)


def test_report_renders(fake_run: Path):
    audit = audit_runs([fake_run])
    md = markdown(audit)
    for heading in ("Per stage", "Per role", "Where a dollar bought nothing", "Routing"):
        assert f"## {heading}" in md
    assert "per passing artifact" in md
    assert "judge" in console(audit)


def test_empty_tree_is_not_an_error(tmp_path: Path):
    audit = audit_runs([tmp_path])
    assert audit.n_runs == 0 and audit.total_usd == 0.0
    assert audit.usd_per_passing_artifact == float("inf")
