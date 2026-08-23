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


@pytest.fixture(autouse=True)
def _clean_agent_env(monkeypatch):
    """Make sure host secrets in the test process do not leak assumptions into tests."""
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", "leak-me")
    yield
