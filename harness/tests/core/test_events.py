"""EventLog durability: a run killed mid-write leaves a partial trailing line,
and `3dcv status` is the first thing anyone types on a run that died (CP-3 / RS-6)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from codeverse.cli.main import app
from codeverse.contracts.common import Backends, Language, Track
from codeverse.contracts.spec import Spec
from codeverse.events import EventLog
from codeverse.workspace import Workspace

runner = CliRunner()


def test_read_skips_a_truncated_last_line(tmp_path: Path) -> None:
    """`emit` writes one buffered line, so a SIGKILL truncates it mid-flush."""
    log = EventLog(tmp_path / "events.jsonl")
    log.emit("round.start", round=0)
    log.emit("build.done", ok=True)
    with log.path.open("a") as fh:  # what a SIGKILL leaves behind
        fh.write('{"t": 1756000001.0, "event": "round.start", "rou')

    evs = log.read()

    assert [e["event"] for e in evs] == ["round.start", "build.done"]


def test_read_skips_a_bad_line_in_the_middle_and_undecodable_bytes(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    p.write_bytes(b'{"event": "a"}\n{"event": "b\xff\n\n{"event": "c"}\n')

    assert [e["event"] for e in EventLog(p).read()] == ["a", "c"]


def test_status_still_prints_the_run_when_events_are_truncated(tmp_path: Path) -> None:
    """The whole status view used to be lost to the last line of the log."""
    runs = tmp_path / "runs"
    ws = Workspace(runs / "stool").create()
    spec = Spec(id="stool", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                prompt="a small wooden stool", backends=Backends(generator="gemini-cli:gemini-3.6-flash"))
    ws.write_json(ws.spec_path, spec)
    log = EventLog(ws.events_path)
    log.emit("run.start")
    with log.path.open("a") as fh:
        fh.write('{"t": 1756000001.0, "event": "round.start", "rou')

    res = runner.invoke(app, ["status", "stool", "--runs-dir", str(runs)])

    assert res.exit_code == 0, res.output
    assert "a small wooden stool" in res.output
    assert "run.start" in res.output
