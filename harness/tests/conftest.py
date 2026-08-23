"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.workspace import Workspace


@pytest.fixture
def tmp_ws(tmp_path: Path) -> Workspace:
    """A fresh, git-initialised workspace under a temp dir."""
    return Workspace(tmp_path / "run").create()
