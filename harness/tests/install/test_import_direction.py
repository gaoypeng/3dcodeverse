"""Keep contracts, proc, conventions, workspace, and judges below their consumers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "codeverse3d"

#: leaf → the codeverse3d packages it must NOT import
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "judges": ("tracks", "addons", "cli", "orchestrator", "agents", "skills", "texturing"),
    "workspace.py": ("tracks", "addons", "cli", "orchestrator", "agents", "skills", "texturing",
                     "spatial", "languages", "reference", "judges", "models", "cost", "config"),
    "contracts": ("tracks", "addons", "cli", "orchestrator", "agents", "workspace", "skills", "texturing",
                  "spatial", "languages", "reference", "judges", "models", "cost", "config", "proc"),
    "proc.py": ("tracks", "addons", "cli", "orchestrator", "agents", "workspace", "skills", "texturing",
                "spatial", "languages", "reference", "judges", "models", "cost", "config", "contracts"),
    "conventions.py": ("tracks", "addons", "cli", "orchestrator", "agents", "workspace", "skills",
                       "texturing", "spatial", "languages", "reference", "judges", "models", "cost", "config"),
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
            if not mod.startswith("codeverse3d."):
                continue
            head = mod.split(".")[1]
            if head in FORBIDDEN[leaf] or f"{head}.py" in FORBIDDEN[leaf]:
                bad.append(f"{f.relative_to(ROOT.parent)}:{lineno} imports {mod}")
    assert not bad, "leaf layer imports upward:\n  " + "\n  ".join(bad)


def test_every_named_layer_exists() -> None:
    """A renamed or merged package must be renamed here too, or its rule silently guards nothing
    (``flywheel``, ``gallery``, ``events`` and ``runlock`` stood here long after they were gone)."""
    names = set(FORBIDDEN) | {n for banned in FORBIDDEN.values() for n in banned}
    assert not [n for n in sorted(names) if not ((ROOT / n).exists() or (ROOT / f"{n}.py").exists())]
