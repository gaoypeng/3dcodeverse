"""``3dcv cost`` and ``bench/cost_report.py``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from codeverse.cli.main import app

runner = CliRunner()


def test_cost_is_registered():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and "cost" in r.stdout


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


def test_bench_cost_report_entry_point(fake_run: Path, tmp_path: Path):
    from bench.cost_report import main

    out = tmp_path / "cost.md"
    assert main([str(fake_run), "--out", str(out), "--quiet"]) == 0
    assert "# Cost report" in out.read_text()
    assert main([str(tmp_path / "nothing"), "--quiet"]) == 2
