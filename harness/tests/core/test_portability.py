"""Packaging and supported-runtime guards (docs/INSTALL.md §2.1).

The harness targets Python 3.13 and Node 20.6+.  These tests pin the setup
script, packaged runtime data and Node's early version gate without requiring
alternate runtimes to be installed.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from codeverse.spatial.node import (
    NODE_MIN,
    NODE_MIN_STR,
    NodeError,
    node_version_error,
    parse_node_version,
    require_node_version,
)
from codeverse.workspace import Workspace

HARNESS = Path(__file__).resolve().parents[2]
PY_FLOOR = (3, 13)


def test_setup_and_runtime_js_declare_the_same_runtime_floors() -> None:
    text = (HARNESS / "setup.sh").read_text()
    assert re.search(rf"^MIN_PY_MINOR={PY_FLOOR[1]}\b", text, re.M), "setup.sh python floor drifted"
    assert re.search(rf"^MIN_NODE_MAJOR={NODE_MIN[0]}\b", text, re.M), "setup.sh node major floor drifted"
    assert re.search(rf"^MIN_NODE_MINOR={NODE_MIN[1]}\b", text, re.M), "setup.sh node minor floor drifted"
    pkg = json.loads((HARNESS / "runtime_js" / "package.json").read_text())
    assert pkg["engines"]["node"] == f">={NODE_MIN_STR}"


def test_package_data_ships_every_file_a_runtime_reads() -> None:
    """Every runtime data file matches a live package-data glob."""
    cfg = tomllib.loads((HARNESS / "pyproject.toml").read_text())
    globs = cfg["tool"]["setuptools"]["package-data"]["codeverse"]
    pkg = HARNESS / "codeverse"
    shipped: set[Path] = set()
    for g in globs:
        hits = {p for p in pkg.glob(g) if p.is_file()}
        assert hits, f"dead package-data glob (matches nothing): {g!r}"
        shipped |= hits
    data = {p for p in pkg.rglob("*")
            if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts}
    missing = sorted(str(p.relative_to(pkg)) for p in data - shipped)
    assert not missing, f"data files no wheel would ship: {missing}"
    excluded = cfg["tool"]["setuptools"]["exclude-package-data"]["codeverse"]
    assert any("__pycache__" in g for g in excluded), "a wheel must not carry the build host's bytecode"


def test_write_example_refuses_to_write_nothing(tmp_path, monkeypatch) -> None:
    """A package missing its starter tree fails instead of writing an empty scene."""
    import codeverse.languages.scene_threejs as skeleton

    monkeypatch.setattr(skeleton, "STARTER_DIR", tmp_path / "gone" / "src")
    with pytest.raises(FileNotFoundError, match="starter tree missing"):
        skeleton.write_example(Workspace(tmp_path / "ws").create())

# ------------------------------------------------------------------- the node floor gate
def test_node_version_parsing_and_gate() -> None:
    parsed = [
        ("v24.14.0\n", (24, 14, 0)),
        ("v20.6.0", (20, 6, 0)),
        ("v18.20.4", (18, 20, 4)),
        ("20.6.1", (20, 6, 1)),
        ("v22.0.0-nightly20240101", (22, 0, 0)),
        ("not found", None),
        ("", None),
    ]
    for text, expected in parsed:
        assert parse_node_version(text) == expected, text
    for version in ((20, 6, 0), (20, 6, 1), (22, 15, 0), (24, 14, 0), None):
        assert node_version_error(version) == "", version
    for version in ((20, 5, 9), (18, 20, 4), (16, 0, 0)):
        msg = node_version_error(version)
        assert ".".join(map(str, version)) in msg
        assert NODE_MIN_STR in msg
        assert "CV3D_BINARIES__NODE" in msg and "nvm" in msg


def test_run_node_enforces_the_version_floor(tmp_path, monkeypatch) -> None:
    import codeverse.spatial.node as node_mod

    script = tmp_path / "noop.mjs"
    script.write_text("console.log('{}')\n")
    monkeypatch.setattr(node_mod, "node_version", lambda _bin: (18, 20, 4))
    monkeypatch.setattr(node_mod, "run_subprocess", lambda *a, **k: pytest.fail("spawned an old node"))
    with pytest.raises(NodeError, match="too old"):
        node_mod.run_node(script)
    monkeypatch.setattr(node_mod, "node_version", lambda _bin: (20, 6, 0))
    require_node_version("node")  # must not raise
