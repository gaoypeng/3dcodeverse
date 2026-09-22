"""Shared pytest fixtures."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from codeverse3d.workspace import Workspace


@pytest.fixture(autouse=True)
def _fresh_settings():
    """`3dcode make --profile X` applies the dial to the cached Settings singleton — right for
    a process that runs one run, but in the test process it leaked into every later test on the
    same xdist worker (`--profile quality` changed the next test's default max_minutes)."""
    from codeverse3d.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def switch(monkeypatch):
    """``switch("C3D_X", "off")`` sets (``None``: unsets) one variable and drops the cached
    Settings, so the next ``get_settings()`` reads it the way a fresh A/B child would."""
    from codeverse3d.config import get_settings

    def set_(name: str, raw: str | None) -> None:
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
        get_settings.cache_clear()

    return set_


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


#: how many tests this session collected.  tests/install/test_hermetic.py needs a
#: ceiling for the counts docs/INSTALL.md quotes, and used to get it by spawning a
#: nested `pytest tests --collect-only` — 8-10 s of the suite's wall clock to learn a
#: number this process already knows.  Under xdist every worker collects the whole
#: suite before running its share, so this is the full count in each of them.
COLLECTED: dict[str, int] = {}


def pytest_collection_modifyitems(session, config, items):  # noqa: ARG001
    COLLECTED["n"] = len(items)
