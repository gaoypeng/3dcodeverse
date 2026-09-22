"""Hermetic-suite and documented test-count guards."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import COLLECTED

HARNESS = Path(__file__).resolve().parents[2]
KEY_ENVS = ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

#: the two that PORT-2 caught; both are inside the offline (pure python) subset
CREDENTIAL_FREE = (
    "tests/models/test_health.py::test_probe_model_treats_a_503_as_final",
    "tests/texturing/test_tools_cli.py::test_texture_pass_tool_runs_with_injected_fakes",
)


def test_passes_with_no_api_keys_in_the_environment(tmp_path: Path) -> None:
    """Credential-sensitive offline tests pass in one key-free subprocess."""
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
    env["HOME"] = str(tmp_path)  # no ~/.config/astra3d/gemini_keys.env either
    env["PYTHONPATH"] = str(HARNESS)

    # -n0: the suite's addopts turn xdist ON, and a nested run must not fork 24 more workers
    r = subprocess.run([sys.executable, "-m", "pytest", *CREDENTIAL_FREE, "-q", "-n0", "-p", "no:cacheprovider"],
                       cwd=HARNESS, env=env, capture_output=True, text=True, timeout=300)

    assert r.returncode == 0, f"needs credentials:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"


# --------------------------------------------------------------------------- PORT-8
INSTALL = HARNESS / "docs" / "INSTALL.md"
#: numbers the docs quote as test counts: "N passed", "N selected", "out of the N"
_COUNT = re.compile(r"(\d{3,6})\s+(?:passed|selected)|out of the (\d{3,6})")


#: below this, the run was narrowed (one file, -k, a single directory) and cannot bound
#: the suite.  The full offline suite is ~1 900.
_FULL_RUN_FLOOR = 1000


def _collected() -> int:
    """Return the collection ceiling, or skip when invoked on a narrow subset."""
    n = COLLECTED.get("n", 0)
    if n < _FULL_RUN_FLOOR:
        pytest.skip(f"only {n} tests collected — a narrowed run cannot bound the suite")
    return n


def test_quoted_test_counts_are_possible() -> None:
    """Published counts cannot exceed what this suite can collect."""
    ceiling = _collected()
    quoted = [int(a or b) for a, b in _COUNT.findall(INSTALL.read_text())]

    assert quoted, f"no test counts found in {INSTALL} — did the wording change?"
    too_big = [n for n in quoted if n > ceiling]
    assert not too_big, f"{INSTALL.name} quotes {too_big}, but only {ceiling} tests exist"


def test_a_build_does_not_dirty_the_tree() -> None:
    paths = ("harness/build/lib/codeverse3d/__init__.py", "harness/dist/x.whl",
             "build/lib/x.py", "dist/x.tar.gz")
    for path in paths:
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=HARNESS.parent)
        assert r.returncode == 0, f"{path} is not gitignored"
