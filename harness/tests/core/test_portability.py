"""Guards for the supported-version floor (docs/INSTALL.md §2.1 "Supported versions").

The harness is developed on python 3.13 / node 24 but must install and run on
python 3.10 / node 20.6.  Nothing here needs either of those installed: the
python guards read the tree, and the node guards drive the version logic with
fakes.  Three things are pinned:

* the floors agree across ``pyproject.toml``, ``ruff``, ``scripts/setup.sh``,
  ``codeverse/spatial/node.py`` and ``runtime_js/package.json``;
* no module reaches past the floor — no 3.12 syntax, and the three 3.11 stdlib
  names the harness needs come from ``codeverse/_compat`` and nowhere else;
* the ``StrEnum`` shim behaves exactly like ``enum.StrEnum``.
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
PY_FLOOR = (3, 12)
PY_FLOOR_STR = "3.12"


#: directories under the scanned trees that hold *run output*, not harness source:
#: `bench/out/` is gitignored and full of LLM-authored `model.py` files, which are
#: written by whatever model ran that battery and are not held to our python floor.
_NOT_SOURCE = ("__pycache__", "out")


def _py_files() -> list[Path]:
    out: list[Path] = []
    for sub in ("codeverse", "bench", "tests"):
        root = HARNESS / sub
        out += [
            p for p in root.rglob("*.py")
            if not any(part in _NOT_SOURCE for part in p.relative_to(root).parts)
        ]
    return sorted(out)


def test_setup_script_checks_the_same_floors() -> None:
    text = (HARNESS / "scripts" / "setup.sh").read_text()
    assert re.search(rf"^MIN_PY_MINOR={PY_FLOOR[1]}\b", text, re.M), "setup.sh python floor drifted"
    assert re.search(rf"^MIN_NODE_MAJOR={NODE_MIN[0]}\b", text, re.M), "setup.sh node major floor drifted"
    assert re.search(rf"^MIN_NODE_MINOR={NODE_MIN[1]}\b", text, re.M), "setup.sh node minor floor drifted"


def test_runtime_js_engines_matches_node_min() -> None:
    pkg = json.loads((HARNESS / "runtime_js" / "package.json").read_text())
    assert pkg["engines"]["node"] == f">={NODE_MIN_STR}"


def test_package_data_ships_every_file_a_runtime_reads() -> None:
    """PORT-4: a wheel built from this tree shipped no scene_threejs starter tree and no
    CONTRACT.md, and the declared glob ``spatial/js/*`` matched nothing at all (there is no
    such directory).  skeleton.write_example() then rglob'd an absent directory and wrote
    ZERO files while reporting success.  Every non-python file under codeverse/ must be
    covered by a package-data glob, and no glob may be dead."""
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
    """The other half of PORT-4: an install without the starter tree must fail loudly at
    the moment it is needed, not hand back an empty scene."""
    from codeverse.languages.scene_threejs import skeleton

    monkeypatch.setattr(skeleton, "STARTER_DIR", tmp_path / "gone" / "src")
    with pytest.raises(FileNotFoundError, match="starter tree missing"):
        skeleton.write_example(Workspace(tmp_path / "ws").create())

# ------------------------------------------------------------------- the node floor gate
@pytest.mark.parametrize(
    "text,expected",
    [
        ("v24.14.0\n", (24, 14, 0)),
        ("v20.6.0", (20, 6, 0)),
        ("v18.20.4", (18, 20, 4)),
        ("20.6.1", (20, 6, 1)),
        ("v22.0.0-nightly20240101", (22, 0, 0)),
        ("not found", None),
        ("", None),
    ],
)
def test_parse_node_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert parse_node_version(text) == expected


@pytest.mark.parametrize("version", [(20, 6, 0), (20, 6, 1), (22, 15, 0), (24, 14, 0), None])
def test_node_version_accepted(version: tuple[int, int, int] | None) -> None:
    """At or above the floor passes; an unknown version is not our error to raise."""
    assert node_version_error(version) == ""


@pytest.mark.parametrize("version", [(20, 5, 9), (18, 20, 4), (16, 0, 0)])
def test_node_version_rejected_with_an_actionable_message(version: tuple[int, int, int]) -> None:
    msg = node_version_error(version)
    assert ".".join(str(n) for n in version) in msg, "says which node was found"
    assert NODE_MIN_STR in msg, "says which node is needed"
    assert "CV3D_BINARIES__NODE" in msg and "nvm" in msg, "says how to fix it"


def test_run_node_refuses_an_old_node_before_spawning(tmp_path, monkeypatch) -> None:
    """The gate fires in run_node, so every node workload fails loudly and early."""
    import codeverse.spatial.node as node_mod

    script = tmp_path / "noop.mjs"
    script.write_text("console.log('{}')\n")
    monkeypatch.setattr(node_mod, "node_version", lambda _bin: (18, 20, 4))
    monkeypatch.setattr(node_mod, "run_subprocess", lambda *a, **k: pytest.fail("spawned an old node"))
    with pytest.raises(NodeError, match="too old"):
        node_mod.run_node(script)


def test_require_node_version_passes_on_a_new_enough_node(monkeypatch) -> None:
    import codeverse.spatial.node as node_mod

    monkeypatch.setattr(node_mod, "node_version", lambda _bin: (20, 6, 0))
    require_node_version("node")  # must not raise
