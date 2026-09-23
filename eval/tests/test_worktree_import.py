"""A bench script run from a worktree must use THAT worktree's `codeverse3d`.

A child spawned by file path that imports codeverse3d before its sys.path bootstrap resolves the
editable install instead: both A/B arms run the same foreign code and the report looks healthy
(voided two A/Bs, 2026-08-25 and the skills wave).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2] / "harness"
BENCH = Path(__file__).resolve().parents[1] / "bench"
CODEVERSE_IMPORT = re.compile(r"^\s*(?:from|import)\s+codeverse3d\b", re.M)
#: every bench module that bootstraps sys.path because it is also run as a script
SCRIPTS = sorted(p for p in BENCH.glob("*.py")
                 if "sys.path.insert" in p.read_text() and CODEVERSE_IMPORT.search(p.read_text()))


@pytest.mark.parametrize("script", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_the_sys_path_bootstrap_comes_before_any_codeverse_import(script: Path):
    text = script.read_text()
    boot = text.index("sys.path.insert")
    first = CODEVERSE_IMPORT.search(text)
    assert first is not None  # filtered by SCRIPTS
    assert first.start() > boot, (
        f"{script.name} imports codeverse3d at line {text[:first.start()].count(chr(10)) + 1}, "
        f"before its sys.path bootstrap at line {text[:boot].count(chr(10)) + 1}. A child "
        f"spawned by file path would resolve codeverse3d through the editable install instead "
        f"of this tree, and the A/B would compare a tree against itself.")


def test_the_guard_refuses_a_foreign_codeverse_for_real(tmp_path: Path):
    """A foreign ``codeverse3d`` pre-imported, then ab_plan run as ``__main__``: it must refuse."""
    import subprocess
    import sys

    other = tmp_path / "other"
    (other / "codeverse3d").mkdir(parents=True)
    (other / "codeverse3d" / "__init__.py").write_text("")
    (other / "codeverse3d" / "_compat.py").write_text("from datetime import timezone as _t\nUTC = _t.utc\n")
    src = (
        "import sys, runpy\n"
        f"sys.path.insert(0, {str(other)!r})\n"
        "import codeverse3d\n"
        "sys.argv = ['ab_plan.py', '--help']\n"
        f"runpy.run_path({str(BENCH / 'ab_plan.py')!r}, run_name='__main__')\n"
    )
    p = subprocess.run([sys.executable, "-c", src], cwd=HARNESS, capture_output=True, text=True, check=False)
    assert p.returncode != 0
    assert "refusing to run" in (p.stdout + p.stderr)
