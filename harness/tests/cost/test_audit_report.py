"""The audit over a tree of runs and its report."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.addons.costreport.audit import (
    audit_runs,
)
from codeverse3d.addons.costreport.report import console, markdown


def test_a_run_that_booked_no_money_still_reports(tmp_path: Path):
    """A $0 run (every session killed before it reported usage) must not divide by zero."""
    ws = tmp_path / "free_run"
    ws.mkdir()
    (ws / "record.json").write_text(json.dumps({
        "spec": {"track": "static_object", "language": "blender", "backends": {"generator": "gemini-cli:gemini-3.7-flash"}},
        "status": "failed", "rounds": [], "total_usage": {}, "extra": {}}))
    ledger = ws / "telemetry" / "cost.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(json.dumps({"run": ws.name, "round": 0, "stage": "baseline", "role": "generator", "backend": "gemini-cli",
                                  "provider": "gemini", "model": "gemini-3.7-flash", "cost_usd": 0.0}) + "\n")
    audit = audit_runs([tmp_path])
    assert audit.n_runs == 1 and audit.total_usd == 0
    assert "0%" in console(audit) and "# Cost audit" in markdown(audit)


def test_a_lower_scoring_round_is_not_waste_but_a_repair_that_never_built_is(fake_run: Path, tmp_path: Path):
    """Any round can be handed over, so a lower score is not waste; a failed repair is."""
    ws = tmp_path / "regressed"
    shutil.copytree(fake_run, ws)
    record = json.loads((ws / "record.json").read_text())
    r1 = json.loads(json.dumps(record["rounds"][0]))
    r1.update(index=1, kind="refine")
    r1["judgment"]["overall"] = 0.5  # worse than r00's 0.8
    r2 = json.loads(json.dumps(r1))
    r2.update(index=2, judgment=None, build={"ok": False, "language": "blender"})
    record["rounds"] += [r1, r2]
    record["total_usage"]["cost_usd"] *= 3
    (ws / "record.json").write_text(json.dumps(record))
    ledger = ws / "telemetry" / "cost.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    ledger.write_text("\n".join(json.dumps({"run": ws.name, "round": i, "stage": stage, "role": "generator",
                                            "label": label, "model": "gemini-3.7-flash", "cost_usd": 0.05})
                                for i, stage, label in ((1, "refine", "refine"), (2, "repair", "r02_refine_repair1")))
                      + "\n")
    kinds = audit_runs([ws]).waste_by_kind()
    assert not {"regression", "zero_delta_round", "unpromoted_judge"} & set(kinds)
    assert kinds["repair_no_converge"] == (1, pytest.approx(0.05))


