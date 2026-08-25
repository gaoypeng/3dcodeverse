"""PORT-2: every non-``live`` test must pass with NO credentials in the environment.

A test that only goes green because this box has 22 Gemini keys is not offline —
it is red on a fresh clone and red on the CI runner, which has none.  Guarded by
re-running the two tests that reached for ambient credentials in a subprocess whose
environment has been stripped of every key source (the env vars AND ``$HOME``, since
the key file lives at ``~/.config/astra3d/gemini_keys.env``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]
KEY_ENVS = ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

#: the two that PORT-2 caught; both are inside the path list .github/workflows/ci.yml runs
CREDENTIAL_FREE = (
    "tests/models/test_health.py::test_probe_model_treats_a_503_as_final",
    "tests/texturing/test_tools_cli.py::test_texture_pass_tool_runs_with_injected_fakes",
)


@pytest.mark.parametrize("nodeid", CREDENTIAL_FREE)
def test_passes_with_no_api_keys_in_the_environment(nodeid: str, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
    env["HOME"] = str(tmp_path)  # no ~/.config/astra3d/gemini_keys.env either
    env["PYTHONPATH"] = str(HARNESS)

    r = subprocess.run([sys.executable, "-m", "pytest", nodeid, "-q", "-p", "no:cacheprovider"],
                       cwd=HARNESS, env=env, capture_output=True, text=True, timeout=300)

    assert r.returncode == 0, f"{nodeid} needs credentials:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
