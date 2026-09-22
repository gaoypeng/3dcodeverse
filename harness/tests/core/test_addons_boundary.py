"""``codeverse3d.addons`` reads finished runs; a run never needs it.  One-way, pinned here."""

from __future__ import annotations

import re
from pathlib import Path

import codeverse3d
import codeverse3d.addons

PKG = Path(codeverse3d.__file__).parent
# an import statement, or a module named in a string (the lazy `_import("codeverse3d.…")` call sites);
# prose that points a reader at the addons is fine
IMPORTS_ADDON = re.compile(r"""(?:\bimport|\bfrom)\s+codeverse3d\.addons|["']codeverse3d\.addons""")


def test_nothing_outside_the_cli_imports_an_addon() -> None:
    offenders = [
        p.relative_to(PKG).as_posix()
        for p in sorted(PKG.rglob("*.py"))
        if p.relative_to(PKG).parts[0] not in {"addons", "cli"} and IMPORTS_ADDON.search(p.read_text())
    ]
    assert offenders == [], f"the core imports an addon: {offenders}"


def test_the_addons_are_the_ones_the_docstring_lists() -> None:
    names = sorted(p.stem for p in (PKG / "addons").iterdir() if p.name not in {"__init__.py", "__pycache__"})
    assert names == ["calibration", "costreport", "dataset", "gallery", "select", "skill_targets"]
    assert all(f"``{n}``" in (codeverse3d.addons.__doc__ or "") for n in names)
