"""Fixtures for the scene runtime tests (C2)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from codeverse3d.languages.scene_threejs import write_example
from codeverse3d.workspace import Workspace

RUNTIME_JS = get_settings().runtime_js_dir()


def node_available() -> bool:
    return shutil.which(get_settings().binaries.node or "node") is not None and (RUNTIME_JS / "node_modules" / "three").is_dir()


def browser_available() -> bool:
    return node_available() and (RUNTIME_JS / "node_modules" / "puppeteer").is_dir()


needs_node = pytest.mark.skipif(not node_available(), reason="node + runtime_js/node_modules required")
needs_browser = pytest.mark.skipif(not browser_available(), reason="puppeteer/chrome required")


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """Empty workspace."""
    return Workspace(tmp_path / "run").create()


@pytest.fixture
def starter_ws(tmp_path: Path) -> Workspace:
    """Workspace with the complete example scene."""
    w = Workspace(tmp_path / "run").create()
    write_example(w)
    return w


def run_node_json(script_body: str, *, cwd: Path = RUNTIME_JS, timeout: int = 60) -> dict | list:
    """Run an inline ESM snippet under runtime_js and parse its last stdout line as JSON."""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script_body], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])
