"""Fixtures for the scene gate tests (reuse the C2 scene runtime helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.languages.scene_threejs import write_example
from codeverse3d.workspace import Workspace


@pytest.fixture
def starter_ws(tmp_path: Path) -> Workspace:
    """Workspace with the complete example scene."""
    w = Workspace(tmp_path / "run").create()
    write_example(w)
    return w
