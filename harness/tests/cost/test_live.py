"""LIVE (needs gemini keys): a metered run's ledger equals its record (docs/COST.md §12)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def test_a_metered_run_reconciles_with_its_own_record(tmp_path: Path):
    """LIVE end-to-end: a real (cheap) run's ledger must equal record.total_usage.

    This is the invariant docs/COST.md §12 reports at 0.000% on two fresh runs;
    the threshold here is the 1% the wave asked for."""
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app
    from codeverse3d.cost.ledger import load_ledger
    from codeverse3d.record.record import load_record
    from codeverse3d.workspace import Workspace

    runs = tmp_path / "runs"
    r = CliRunner().invoke(app, [
        "make", "a smooth grey ceramic bowl", "--track", "static_object", "--language", "blender",
        "--generator", "single-shot:gemini:gemini-3.8-flash", "--judge", "gemini:gemini-3.8-flash",
        "--rounds", "0", "--max-minutes", "15",
        "--runs-dir", str(runs), "--slug", "cost_live_bowl"])
    assert r.exit_code in (0, 1), r.output  # a failed judge/build is still a metered run
    ws = Workspace(runs / "cost_live_bowl")
    rows = load_ledger(ws.root)
    assert rows and (ws.root / "telemetry" / "cost.jsonl").is_file()
    assert all(row.run == "cost_live_bowl" for row in rows)
    assert {row.stage.value for row in rows} >= {"plan", "baseline"}
    recorded = load_record(ws).total_usage.cost_usd
    ledger = sum(row.cost_usd for row in rows)
    assert recorded > 0 and abs(ledger - recorded) / recorded < 0.01, f"{ledger} vs {recorded}"
