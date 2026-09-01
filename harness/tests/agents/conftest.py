"""Fixtures for agent tests: fake CLI binaries that mimic each tool's output."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest


def write_fake_cli(path: Path, body: str) -> Path:
    """Write an executable python script ``path`` whose main body is ``body``.

    The script receives argv like the real CLI and must print to stdout.
    """
    path.write_text("#!" + sys.executable + "\nimport sys, os, json, time\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def fake_bin(tmp_path: Path):
    d = tmp_path / "bin"
    d.mkdir()

    def make(name: str, body: str) -> str:
        return str(write_fake_cli(d / name, body))

    return make


@pytest.fixture
def scoped_ws(tmp_ws):
    """A workspace with an entry file and two part files — the tree every write-scope
    test (the CLI post-hoc restore and the single-shot envelope alike) needs."""
    (tmp_ws.src / "parts").mkdir(parents=True, exist_ok=True)
    (tmp_ws.src / "model.py").write_text("# entry\n")
    (tmp_ws.src / "parts" / "seat.py").write_text("# seat\n")
    (tmp_ws.src / "parts" / "leg.py").write_text("# leg\n")
    return tmp_ws


@pytest.fixture(autouse=True)
def _clean_agent_env(monkeypatch):
    """Make sure host secrets in the test process do not leak assumptions into tests."""
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", "leak-me")
    yield


@pytest.fixture(autouse=True)
def _fast_watchdog_poll(monkeypatch):
    """A fake CLI exits in milliseconds; the watchdog's 1 s poll then dominated every
    agent.run() in this directory (~13 s across the suite).  Production keeps 1 s —
    a real vendor CLI runs for minutes and does not care."""
    monkeypatch.setattr("codeverse.agents.cli_common.POLL_S", 0.02)
