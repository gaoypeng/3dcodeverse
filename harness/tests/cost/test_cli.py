"""``3dcode cost``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.cli.main import app

runner = CliRunner()


def test_cost_show(fake_run: Path, tmp_path: Path):
    out = tmp_path / "report.md"
    r = runner.invoke(app, ["cost", "show", str(fake_run), "--md", str(out)])
    assert r.exit_code == 0, r.output
    assert "passing" in r.stdout and "judge" in r.stdout
    assert out.is_file() and "## Per stage" in out.read_text()


def test_cost_show_recheck_reports_drift(fake_run: Path):
    r = runner.invoke(app, ["cost", "show", str(fake_run), "--recheck"])
    assert r.exit_code == 0 and "re-priced total" in r.output


def test_cost_show_on_an_empty_tree_fails_cleanly(tmp_path: Path):
    r = runner.invoke(app, ["cost", "show", str(tmp_path)])
    assert r.exit_code != 0 and "no runs" in r.output


def test_cost_prices_and_estimate():
    r = runner.invoke(app, ["cost", "prices", "--provider", "gemini"])
    assert r.exit_code == 0 and "gemini:gemini-3.7-flash" in r.stdout and "verified" in r.stdout
    r = runner.invoke(app, ["cost", "estimate", "gemini:gemini-3.7-flash", "--in", "100000", "--out", "1000"])
    assert r.exit_code == 0 and "$0.0788" in r.stdout


# --------------------------------------------------------------------- the live ledger
def _live_ledger(run: Path, rows: list[dict]) -> None:
    p = run / "telemetry" / "cost.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_cost_takes_a_bare_run_path_or_slug(fake_run: Path):
    r = runner.invoke(app, ["cost", str(fake_run)])
    assert r.exit_code == 0, r.output
    assert "reconciliation" in r.output and "reconstructed" in r.output
    r = runner.invoke(app, ["cost", fake_run.name, "--runs-dir", str(fake_run.parent)])
    assert r.exit_code == 0 and "reconciliation" in r.output


def test_cost_runs_dir_aggregates_a_battery(fake_run: Path, tmp_path: Path):
    r = runner.invoke(app, ["cost", "--runs-dir", str(fake_run.parent)])
    assert r.exit_code == 0 and "runs: 1" in r.output


def test_a_live_ledger_is_read_instead_of_being_reconstructed(fake_run: Path):
    """The run wrote its own rows while it ran: they ARE the ledger."""
    _live_ledger(fake_run, [
        {"run": fake_run.name, "round": 0, "stage": "baseline", "role": "generator",
         "backend": "gemini", "provider": "gemini", "model": "gemini-3.7-flash",
         "input_tokens": 30_000, "cached_tokens": 10_000, "output_tokens": 300,
         "cost_usd": 0.0201, "price_source": "exact"},
        {"run": fake_run.name, "round": 0, "stage": "judge", "role": "judge",
         "backend": "gemini", "provider": "gemini", "model": "gemini-3.1-pro-preview",
         "input_tokens": 12_000, "output_tokens": 1_000, "cost_usd": 0.0999, "price_source": "exact"},
    ])
    r = runner.invoke(app, ["cost", str(fake_run)])
    assert r.exit_code == 0, r.output
    assert "1 live / 0 reconstructed" in r.output
    assert "$0.0999" in r.output  # the ledger's own judge dollar, not the reconstruction's $0.036
    # what the ledger does not explain is reported, never hidden
    assert "unattributed" in r.output or "difference" in r.output


def test_cost_reports_the_calls_per_key_when_the_ledger_recorded_them(fake_run: Path, tmp_path: Path):
    """audit 2026-08-26 §4: no row said which key served a call, so 'is one key hammered' could
    only be probed.  Live rows now carry the key suffix and the round-trip count; the report
    shows both, and a ledger without them (older, or reconstructed) simply has no key section."""
    base = {"run": fake_run.name, "round": 0, "stage": "baseline", "role": "generator", "backend": "gemini",
            "provider": "gemini", "model": "gemini-3.7-flash", "input_tokens": 1_000, "output_tokens": 10,
            "cost_usd": 0.001, "price_source": "exact"}
    _live_ledger(fake_run, [dict(base, key="…k1", attempts=1), dict(base, key="…k1", attempts=3),
                            dict(base, key="…k2", attempts=1)])
    r = runner.invoke(app, ["cost", str(fake_run), "--md", str(tmp_path / "c.md")])
    assert r.exit_code == 0, r.output
    assert "key (last 4 chars):" in r.output and "…k1" in r.output and "tries/call 2.00" in r.output
    md = (tmp_path / "c.md").read_text()
    assert "## Per API key" in md and "tries/call" in md


def test_cost_cache_reports_the_cold_head_of_every_session(fake_run: Path):
    _live_ledger(fake_run, [
        {"run": "x", "round": 0, "stage": "baseline", "label": "api-agent:baseline:t0",
         "provider": "gemini", "model": "gemini-3.7-flash", "input_tokens": 12_000,
         "cached_tokens": 0, "cost_usd": 0.009, "price_input": 0.75, "price_cached": 0.075},
        {"run": "x", "round": 0, "stage": "baseline", "label": "api-agent:baseline:t1",
         "provider": "gemini", "model": "gemini-3.7-flash", "input_tokens": 20_000,
         "cached_tokens": 12_000, "cost_usd": 0.007, "price_input": 0.75, "price_cached": 0.075},
    ])
    r = runner.invoke(app, ["cost", "cache", str(fake_run)])
    assert r.exit_code == 0, r.output
    assert "r0:baseline" in r.output and "12,000" in r.output
    assert "cold heads cost" in r.output


def test_cost_prices_flags_stale_and_approximate_rows():
    r = runner.invoke(app, ["cost", "prices", "--stale"])
    assert r.exit_code == 0
    assert "flags" in r.stdout and ("approximate" in r.stdout or "none older than" in r.stdout)
    r = runner.invoke(app, ["cost", "prices", "--days", "-1"])
    assert r.exit_code == 0 and "stale>-1d" in r.stdout  # nothing is younger than -1 days


def test_cost_show_still_works_the_long_way(fake_run: Path):
    r = runner.invoke(app, ["cost", "show", str(fake_run), "--no-per-run"])
    assert r.exit_code == 0
