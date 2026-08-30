"""A bench script run from a worktree must use THAT worktree's `codeverse`.

WHY this is worth a test of its own: it has silently voided two A/B runs.
``ab_plan.spawn_cell`` starts each child as a FILE path, so ``sys.path[0]`` is ``bench/``
and the cwd is not on the path.  Any ``import codeverse`` before the script's own
``sys.path`` bootstrap therefore resolves through whatever editable install is present
(``__editable__.3dcodeverse-<v>.pth`` installs a meta-path finder pinned to the tree it was
installed from).  Both arms then run the same foreign code, every switch under test is
inert, and the report looks completely healthy — the failure has no symptom at all.

Caught on 2026-08-25 in `bench/out/plan_loop/C0/invalid_attempt1_maintree_import` (worked
around in a launch script) and again in the skills wave's first A/B, where the variant arm
ran a tree with no skills package and materialised nothing.  A launch-script workaround
protects whoever remembers it; these two checks protect the run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]
BENCH = HARNESS / "bench"
CODEVERSE_IMPORT = re.compile(r"^\s*(?:from|import)\s+codeverse\b", re.M)
#: every bench module that bootstraps sys.path because it is also run as a script
SCRIPTS = sorted(p for p in BENCH.glob("*.py")
                 if "sys.path.insert" in p.read_text() and CODEVERSE_IMPORT.search(p.read_text()))


def test_there_is_at_least_one_such_script():
    assert SCRIPTS, "no bench script bootstraps sys.path any more — has the layout changed?"


@pytest.mark.parametrize("script", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_the_sys_path_bootstrap_comes_before_any_codeverse_import(script: Path):
    text = script.read_text()
    boot = text.index("sys.path.insert")
    first = CODEVERSE_IMPORT.search(text)
    assert first is not None  # filtered by SCRIPTS
    assert first.start() > boot, (
        f"{script.name} imports codeverse at line {text[:first.start()].count(chr(10)) + 1}, "
        f"before its sys.path bootstrap at line {text[:boot].count(chr(10)) + 1}. A child "
        f"spawned by file path would resolve codeverse through the editable install instead "
        f"of this tree, and the A/B would compare a tree against itself.")


def test_the_guard_refuses_a_foreign_codeverse_for_real(tmp_path: Path):
    """Not "the string is in the file": actually make the bad thing happen.

    A foreign ``codeverse`` is pre-imported into ``sys.modules``, then ab_plan is run as
    ``__main__``.  It must die with its own message rather than proceed to compare a tree
    with itself.
    """
    import subprocess
    import sys

    other = tmp_path / "other"
    (other / "codeverse").mkdir(parents=True)
    (other / "codeverse" / "__init__.py").write_text("")
    (other / "codeverse" / "_compat.py").write_text("from datetime import timezone as _t\nUTC = _t.utc\n")
    src = (
        "import sys, runpy\n"
        f"sys.path.insert(0, {str(other)!r})\n"
        "import codeverse\n"
        "sys.argv = ['ab_plan.py', '--help']\n"
        f"runpy.run_path({str(BENCH / 'ab_plan.py')!r}, run_name='__main__')\n"
    )
    p = subprocess.run([sys.executable, "-c", src], cwd=HARNESS, capture_output=True, text=True, check=False)
    assert p.returncode != 0
    assert "refusing to run" in (p.stdout + p.stderr)


def test_the_guard_actually_compares_the_resolved_package_to_this_tree():
    import codeverse
    from bench.ab_plan import REPO, _assert_local_codeverse

    assert Path(codeverse.__file__).resolve().parent == REPO / "codeverse", (
        "the test suite itself is importing a foreign codeverse")
    _assert_local_codeverse()          # must not raise in a correctly-resolved tree
