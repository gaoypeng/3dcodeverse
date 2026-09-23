"""``codeverse3d.addons`` reads finished runs; a run never needs it.  One-way, pinned here."""

from __future__ import annotations

import re
from pathlib import Path

import codeverse3d

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
