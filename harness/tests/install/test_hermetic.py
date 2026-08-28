"""Guards for claims the install docs make about the suite.

PORT-2: every non-``live`` test must pass with NO credentials in the environment.

A test that only goes green because this box has 22 Gemini keys is not offline —
it is red on a fresh clone (which has none).  Guarded by
re-running the two tests that reached for ambient credentials in a subprocess whose
environment has been stripped of every key source (the env vars AND ``$HOME``, since
the key file lives at ``~/.config/astra3d/gemini_keys.env``).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]
KEY_ENVS = ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

#: the two that PORT-2 caught; both are inside the offline (pure python) subset
CREDENTIAL_FREE = (
    "tests/models/test_health.py::test_probe_model_treats_a_503_as_final",
    "tests/texturing/test_tools_cli.py::test_texture_pass_tool_runs_with_injected_fakes",
)


@pytest.mark.parametrize("nodeid", CREDENTIAL_FREE)
def test_passes_with_no_api_keys_in_the_environment(nodeid: str, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
    env["HOME"] = str(tmp_path)  # no ~/.config/astra3d/gemini_keys.env either
    env["PYTHONPATH"] = str(HARNESS)

    # -n0: the suite's addopts turn xdist ON, and a nested run must not fork 24 more workers
    r = subprocess.run([sys.executable, "-m", "pytest", nodeid, "-q", "-n0", "-p", "no:cacheprovider"],
                       cwd=HARNESS, env=env, capture_output=True, text=True, timeout=300)

    assert r.returncode == 0, f"{nodeid} needs credentials:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"


# --------------------------------------------------------------------------- PORT-8
INSTALL = HARNESS / "docs" / "INSTALL.md"
#: numbers the docs quote as test counts: "N passed", "N selected", "out of the N"
_COUNT = re.compile(r"(\d{3,6})\s+(?:passed|selected)|out of the (\d{3,6})")


def _collected() -> int:
    """How many tests the suite actually has (the ceiling for any quoted count)."""
    r = subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-n0",
                        "-p", "no:cacheprovider"], cwd=HARNESS, capture_output=True, text=True, timeout=300)
    m = re.search(r"(\d+)\s*/\s*(\d+) tests collected", r.stdout) or re.search(r"(\d+) tests collected", r.stdout)
    assert m, r.stdout[-2000:]
    return int(m.group(m.lastindex))


@pytest.mark.parametrize("doc", [INSTALL], ids=["INSTALL.md"])
def test_quoted_test_counts_are_possible(doc: Path) -> None:
    """docs/INSTALL.md §2.1 advertises the floor as 'a tested claim, not an aspiration',
    so a count no invocation can produce is worse than no count: it read as a broken
    install to anyone comparing their own run.  §2.1 claimed 2413 passed and ci.yml
    1809 for a subset, against a tree that could collect neither (PORT-8).

    Deliberately a ceiling, not an equality — pinning exact counts would fail on every
    added test, which is how they went stale in the first place."""
    ceiling = _collected()
    quoted = [int(a or b) for a, b in _COUNT.findall(doc.read_text())]

    assert quoted, f"no test counts found in {doc} — did the wording change?"
    too_big = [n for n in quoted if n > ceiling]
    assert not too_big, f"{doc.name} quotes {too_big}, but only {ceiling} tests exist"


@pytest.mark.parametrize("path", ["harness/build/lib/codeverse/__init__.py", "harness/dist/x.whl",
                                  "build/lib/x.py", "dist/x.tar.gz"])
def test_a_build_does_not_dirty_the_tree(path: str) -> None:
    """`pip wheel ./harness` leaves a full build/lib/codeverse copy, which showed up as
    `?? harness/build/` in a fresh clone's `git status` (PORT-8).  A stale build/lib is
    also a setuptools footgun for the next wheel."""
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=HARNESS.parent)

    assert r.returncode == 0, f"{path} is not gitignored"
