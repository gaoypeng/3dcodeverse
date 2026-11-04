"""The audit over a tree of runs and its report."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.addons.costreport.audit import (
    audit_runs,
)


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
                                            "label": label, "model": "gemini-3.8-flash", "cost_usd": 0.05})
                                for i, stage, label in ((1, "refine", "refine"), (2, "repair", "r02_refine_repair1")))
                      + "\n")
    kinds = audit_runs([ws]).waste_by_kind()
    assert not {"regression", "zero_delta_round", "unpromoted_judge"} & set(kinds)
    assert kinds["repair_no_converge"] == (1, pytest.approx(0.05))


