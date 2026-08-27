"""Shared pytest fixtures."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from codeverse.workspace import Workspace


@pytest.fixture
def tmp_ws(tmp_path: Path) -> Workspace:
    """A fresh, git-initialised workspace under a temp dir."""
    return Workspace(tmp_path / "run").create()


def assert_pid_gone(pid: int, *, group: bool = False, timeout_s: float = 10.0) -> None:
    """Block until ``pid`` (``group=True``: its whole process group) is dead and reaped.

    A reaped zombie still answers ``kill(pid, 0)`` on some kernels, so ``State: Z``
    counts as gone for a single pid; for a GROUP it does not — an unreaped zombie
    leader keeps the group "alive" and is exactly the bug the group probes watch for.
    """
    probe = os.killpg if group else os.kill
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            probe(pid, 0)
        except ProcessLookupError:
            return
        status = Path(f"/proc/{pid}/status")
        if not group and status.exists() and "State:\tZ" in status.read_text():
            return
        time.sleep(0.05)
    raise AssertionError(f"{'process group' if group else 'pid'} {pid} survived the kill")
