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

import ast
import enum
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codeverse._compat import UTC, StrEnum, tomllib
from codeverse.spatial.node import (
    NODE_MIN,
    NODE_MIN_STR,
    NodeError,
    node_version_error,
    parse_node_version,
    require_node_version,
)

HARNESS = Path(__file__).resolve().parents[2]
PY_FLOOR = (3, 10)
PY_FLOOR_STR = "3.10"
COMPAT = HARNESS / "codeverse" / "_compat.py"

#: stdlib names added in 3.11 that must be imported from ``codeverse._compat`` instead
FLOOR_VIOLATIONS = {
    ("enum", "StrEnum"): "codeverse._compat.StrEnum",
    ("datetime", "UTC"): "codeverse._compat.UTC",
    ("tomllib", None): "codeverse._compat.tomllib",
}


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


# --------------------------------------------------------------------- the floors agree
def test_pyproject_floor_matches_ruff_target() -> None:
    cfg = tomllib.loads((HARNESS / "pyproject.toml").read_text())
    assert cfg["project"]["requires-python"] == f">={PY_FLOOR_STR}"
    assert cfg["tool"]["ruff"]["target-version"] == "py" + PY_FLOOR_STR.replace(".", "")
    classifiers = cfg["project"]["classifiers"]
    assert f"Programming Language :: Python :: {PY_FLOOR_STR}" in classifiers


def test_setup_script_checks_the_same_floors() -> None:
    text = (HARNESS / "scripts" / "setup.sh").read_text()
    assert re.search(rf"^MIN_PY_MINOR={PY_FLOOR[1]}\b", text, re.M), "setup.sh python floor drifted"
    assert re.search(rf"^MIN_NODE_MAJOR={NODE_MIN[0]}\b", text, re.M), "setup.sh node major floor drifted"
    assert re.search(rf"^MIN_NODE_MINOR={NODE_MIN[1]}\b", text, re.M), "setup.sh node minor floor drifted"


def test_runtime_js_engines_matches_node_min() -> None:
    pkg = json.loads((HARNESS / "runtime_js" / "package.json").read_text())
    assert pkg["engines"]["node"] == f">={NODE_MIN_STR}"


# ------------------------------------------------------------------- nothing exceeds it
@pytest.mark.parametrize("path", _py_files(), ids=lambda p: str(p.relative_to(HARNESS)))
def test_module_parses_and_stays_on_the_floor(path: Path) -> None:
    """Floor-version syntax only, and no direct import of a 3.11-only stdlib name."""
    source = path.read_text()
    try:
        tree = ast.parse(source, filename=str(path), feature_version=PY_FLOOR)
    except SyntaxError as e:  # PEP 695 generics / `type X = ...` / anything newer
        pytest.fail(f"{path.relative_to(HARNESS)} does not parse on python {PY_FLOOR_STR}: {e.msg}")

    if path == COMPAT:
        return  # the one module allowed to name them
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                use = FLOOR_VIOLATIONS.get((node.module, alias.name))
                if use:
                    bad.append(f"line {node.lineno}: from {node.module} import {alias.name} -> use {use}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                use = FLOOR_VIOLATIONS.get((alias.name, None))
                if use:
                    bad.append(f"line {node.lineno}: import {alias.name} -> use {use}")
    assert not bad, f"{path.relative_to(HARNESS)}: 3.11+ stdlib name(s) below the floor:\n  " + "\n  ".join(bad)


# ------------------------------------------------------------------------ the shims work
class Colour(StrEnum):
    RED = "red"
    DEEP_BLUE = enum.auto()


#: 3.10 has no ``enum.StrEnum`` to compare against — there the shim is the only
#: implementation, and the absolute assertions below are the whole contract.
requires_stdlib_strenum = pytest.mark.skipif(
    not hasattr(enum, "StrEnum"), reason="enum.StrEnum needs python 3.11+"
)


def _std_colour() -> type:
    class StdColour(enum.StrEnum):  # type: ignore[attr-defined]
        RED = "red"
        DEEP_BLUE = enum.auto()

    return StdColour


def test_str_enum_shim_behaviour() -> None:
    """What every call site relies on: members are their value, everywhere."""
    assert str(Colour.RED) == "red"
    assert f"{Colour.RED}" == "red" and f"{Colour.RED:>5}" == "  red"
    assert "%s" % Colour.RED == "red"  # noqa: UP031 — %-formatting is exactly what this asserts
    assert json.dumps(Colour.RED) == '"red"'
    assert repr(Colour.RED) == "<Colour.RED: 'red'>"
    assert isinstance(Colour.RED, str) and Colour.RED == "red" and hash(Colour.RED) == hash("red")
    assert Colour.DEEP_BLUE == "deep_blue", "auto() must lower-case the member name"
    assert Colour("red") is Colour.RED
    assert sorted(Colour) == ["deep_blue", "red"]


@requires_stdlib_strenum
def test_str_enum_shim_matches_the_stdlib() -> None:
    """On 3.11+ the shim must be indistinguishable from ``enum.StrEnum``."""
    std_colour = _std_colour()
    for name in ("RED", "DEEP_BLUE"):
        shim, std = Colour[name], std_colour[name]
        assert str(shim) == str(std)
        assert f"{shim}" == f"{std}"
        assert f"{shim:>8}" == f"{std:>8}"
        assert "%s" % shim == "%s" % std  # noqa: UP031 — %-formatting is exactly what this asserts
        assert json.dumps(shim) == json.dumps(std)
        assert repr(shim).split(".", 1)[1] == repr(std).split(".", 1)[1]
        assert shim.value == std.value and shim.name == std.name


def test_str_enum_shim_serialises_through_pydantic() -> None:
    from pydantic import BaseModel

    class M(BaseModel):
        shim: Colour

    m = M(shim="red")
    assert m.model_dump_json() == '{"shim":"red"}'
    assert m.model_dump(mode="json") == {"shim": "red"}
    assert M.model_json_schema()["$defs"]["Colour"]["enum"] == ["red", "deep_blue"]


def test_utc_shim() -> None:
    assert UTC is timezone.utc
    assert datetime.now(UTC).tzinfo is UTC


def test_tomllib_shim_parses() -> None:
    assert tomllib.loads('a = 1\n[t]\nb = "x"\n') == {"a": 1, "t": {"b": "x"}}


def test_compat_documents_every_shim() -> None:
    """Each exported shim must say in the module docstring when it can be deleted."""
    import codeverse._compat as compat

    doc = compat.__doc__ or ""
    for name in compat.__all__:
        assert name in doc, f"codeverse/_compat.py docstring never mentions {name}"
    assert "3.11" in doc


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
