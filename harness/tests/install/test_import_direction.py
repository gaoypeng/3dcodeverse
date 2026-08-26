"""The leaf layer must not import upward — the layering the review found valuable and unwritten.

``judges/`` imports nothing from tracks / flywheel / cli / orchestrator / agents / workspace while
every one of those imports ``judges/``: a real, load-bearing direction (it is why the 2026-08-26
review's C3 stopped short of moving ``build_judge_input`` into judges — doing so would invert
every edge).  ``contracts/``, ``proc.py`` and ``conventions.py`` are pure leaves: data, stdlib
helpers, and the frames/units/naming table (CLAUDE.md law 2 says "import, never restate").

Nothing enforced any of this: ``test_docs`` pins the package MAP, not the import DIRECTION.
Measured 2026-08-26 before writing this test, the direction held exactly as stated — judges does
import ``spatial`` (sheet, measure, silhouette, render) and ``reference``, which is legitimate and
allowed here.  The point is to catch the next parallel wave's inversion before review has to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "codeverse"

#: leaf → the codeverse packages it must NOT import
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "judges": ("tracks", "flywheel", "cli", "orchestrator", "agents", "workspace", "gallery", "skills", "texturing"),
    "contracts": tuple(p for p in ("tracks", "flywheel", "cli", "orchestrator", "agents", "workspace", "gallery",
                                   "skills", "texturing", "spatial", "languages", "reference", "judges", "models",
                                   "cost", "config", "events", "runlock", "proc")),
    "proc.py": ("tracks", "flywheel", "cli", "orchestrator", "agents", "workspace", "gallery", "skills", "texturing",
                "spatial", "languages", "reference", "judges", "models", "cost", "config", "events", "contracts"),
    "conventions.py": ("tracks", "flywheel", "cli", "orchestrator", "agents", "workspace", "gallery", "skills",
                       "texturing", "spatial", "languages", "reference", "judges", "models", "cost", "config", "events"),
}


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
    return out


@pytest.mark.parametrize("leaf", sorted(FORBIDDEN))
def test_leaf_layer_never_imports_upward(leaf: str) -> None:
    target = ROOT / leaf
    files = [target] if target.is_file() else sorted(target.rglob("*.py"))
    assert files, f"{leaf} vanished — update this test with the package map"
    bad = []
    for f in files:
        for lineno, mod in _imports(f):
            if not mod.startswith("codeverse."):
                continue
            head = mod.split(".")[1]
            if head in FORBIDDEN[leaf] or f"{head}.py" in FORBIDDEN[leaf]:
                bad.append(f"{f.relative_to(ROOT.parent)}:{lineno} imports {mod}")
    assert not bad, "leaf layer imports upward:\n  " + "\n  ".join(bad)
