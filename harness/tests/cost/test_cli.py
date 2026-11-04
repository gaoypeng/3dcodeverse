"""``3dcode cost``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.cli.main import app

runner = CliRunner()


def test_cost_prices_and_estimate():
    r = runner.invoke(app, ["cost", "prices", "--provider", "gemini"])
    assert r.exit_code == 0 and "gemini:gemini-3.8-flash" in r.stdout and "verified" in r.stdout
    r = runner.invoke(app, ["cost", "estimate", "gemini:gemini-3.8-flash", "--in", "100000", "--out", "1000"])
    assert r.exit_code == 0 and "$0.0788" in r.stdout
    r = runner.invoke(app, ["cost", "estimate", "gemini:gemini-3.8-flash", "--in", "12000", "--images", "8"])
    assert r.exit_code == 0 and "~22,320 in" in r.stdout, r.stdout  # 12 000 + 8 x 1 290


# --------------------------------------------------------------------- the live ledger
def _live_ledger(run: Path, rows: list[dict]) -> None:
    p = run / "telemetry" / "cost.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_cost_takes_a_bare_run_path_a_slug_or_a_battery(fake_run: Path):
    r = runner.invoke(app, ["cost", str(fake_run)])
    assert r.exit_code == 0, r.output
    assert "| fake_run | static_object |" in r.output
    r = runner.invoke(app, ["cost", "--runs-dir", str(fake_run.parent)])
    assert r.exit_code == 0 and "runs: 1" in r.output


def test_the_ledger_is_the_money(fake_run: Path):
    """The ledger rows are the money, whatever the record says (D84)."""
    _live_ledger(fake_run, [
        {"run": fake_run.name, "round": 0, "stage": "baseline", "role": "generator",
         "backend": "gemini", "provider": "gemini", "model": "gemini-3.8-flash",
         "input_tokens": 30_000, "cached_tokens": 10_000, "output_tokens": 300,
         "cost_usd": 0.0201, "price_source": "exact"},
        {"run": fake_run.name, "round": 0, "stage": "judge", "role": "judge",
         "backend": "gemini", "provider": "gemini", "model": "gemini-3.1-pro-preview",
         "input_tokens": 12_000, "output_tokens": 1_000, "cost_usd": 0.0999, "price_source": "exact"},
    ])
    r = runner.invoke(app, ["cost", str(fake_run)])
    assert r.exit_code == 0, r.output
    assert "$0.0999" in r.output and "$0.1200" in r.output  # the ledger's own dollars


