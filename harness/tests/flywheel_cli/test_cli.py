"""CLI tests (typer CliRunner, offline): make --no-run, status, flywheel, doctor pieces, tools list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse.cli import _common as C
from codeverse.cli.main import app
from codeverse.contracts.spec import Spec

runner = CliRunner()


def test_help_and_version():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and "flywheel" in r.output and "doctor" in r.output
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "c3v" in r.output


def test_make_no_run_creates_valid_workspace(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a wooden dining chair", "--track", "static_object", "--language", "blender",
                            "--runs-dir", str(runs), "--no-run", "--dim", "width=0.5", "--must", "four legs",
                            "--rounds", "2", "--max-usd", "1.5", "--generator", "gemini-cli:gemini-3.7-flash"])
    assert r.exit_code == 0, r.output
    dirs = [d for d in runs.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    ws = dirs[0]
    assert ws.name.startswith("a_wooden_dining_chair_") and (ws / "spec.json").is_file() and (ws / ".git").is_dir()
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.constraints.dimensions_m == {"width": 0.5} and spec.constraints.must_have == ["four legs"]
    assert spec.budget.max_rounds == 2 and spec.budget.max_usd == 1.5
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    assert (ws / "src").is_dir() and (ws / "artifacts" / "renders").is_dir()
    # same prompt again → same slug → refuse without --force
    r2 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run"])
    assert r2.exit_code == 1 and "already exists" in (r2.output + str(r2.stderr if hasattr(r2, "stderr") else ""))
    r3 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run", "--force"])
    assert r3.exit_code == 0
    # invalid language/track combination → typed error
    r4 = runner.invoke(app, ["make", "x", "--track", "scene", "--language", "blender", "--runs-dir", str(runs), "--no-run", "--slug", "bad"])
    assert r4.exit_code == 1


def test_status_on_fake_run(runs_dir: Path):
    r = runner.invoke(app, ["status", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 0, r.output
    assert "passed" in r.output and "0.800" in r.output and "rounds" in r.output
    r = runner.invoke(app, ["status", "nope", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 1


def test_flywheel_commands(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    r = runner.invoke(app, ["flywheel", "export", str(runs_dir), str(out), "--pack"])
    assert r.exit_code == 0, r.output
    assert (out / "metadata.parquet").is_file() and (out / "samples-000.tar").is_file()
    r = runner.invoke(app, ["flywheel", "pairs", str(runs_dir), str(tmp_path / "p.jsonl")])
    assert r.exit_code == 0 and "pairs" in r.output
    r = runner.invoke(app, ["flywheel", "index", str(runs_dir), str(tmp_path / "i.sqlite")])
    assert r.exit_code == 0 and "indexed 3 runs" in r.output
    r = runner.invoke(app, ["flywheel", "dedupe", str(out), "--no-mesh"])
    assert r.exit_code == 0 and "3 samples" in r.output


def test_tools_list_and_unknown(tmp_path: Path):
    r = runner.invoke(app, ["tools", "list"])
    # spatial tools may or may not be installed yet; either a table or a clear message, never a traceback
    assert r.exit_code in (0, 2), r.output
    assert "Traceback" not in r.output


def test_lazy_import_message():
    with pytest.raises(C.CliError):
        C.lazy("codeverse.definitely_missing_module")


def test_doctor_json(tmp_path: Path):
    r = runner.invoke(app, ["doctor", "--no-gpu", "--json"])
    assert r.exit_code in (0, 1), r.output
    rows = json.loads(r.output[r.output.index("["):])
    checks = {row["check"] for row in rows}
    assert {"python", "python deps", "blender", "node", "three", "gemini keys", "git", "mcp"} <= checks
