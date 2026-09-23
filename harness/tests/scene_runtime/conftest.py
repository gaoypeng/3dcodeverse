"""Fixtures for the scene runtime tests (C2)."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from codeverse3d.languages.scene_threejs import write_example
from codeverse3d.spatial.node import run_node
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


def run_node_json(script_body: str, *, timeout: int = 60) -> dict | list:
    """Run an ESM snippet whose ``./lib/`` is runtime_js/lib and parse its last stdout line as JSON."""
    with tempfile.TemporaryDirectory(prefix="c3d-node-json-") as tmp:
        (Path(tmp) / "lib").symlink_to(RUNTIME_JS / "lib")
        (Path(tmp) / "probe.mjs").write_text(script_body, encoding="utf-8")
        proc = run_node(Path(tmp) / "probe.mjs", cwd=tmp, timeout_s=timeout, three_hook=True, check=False)
    assert proc.rc == 0, proc.stderr_tail
    return json.loads(proc.stdout.strip().splitlines()[-1])
