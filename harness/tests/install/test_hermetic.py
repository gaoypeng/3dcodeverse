"""Hermetic-suite guard: credential-sensitive offline tests pass without API keys."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[2]
KEY_ENVS = ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

#: the one that PORT-2 caught; inside the offline (pure python) subset
CREDENTIAL_FREE = ("tests/models/test_health.py::test_probe_model_treats_a_503_as_final",)


def test_passes_with_no_api_keys_in_the_environment(tmp_path: Path) -> None:
    """Credential-sensitive offline tests pass in one key-free subprocess."""
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
    env["HOME"] = str(tmp_path)  # no ~/.config/astra3d/gemini_keys.env either
    env["PYTHONPATH"] = str(HARNESS)

    # -n0: the suite's addopts turn xdist ON, and a nested run must not fork 24 more workers
    r = subprocess.run([sys.executable, "-m", "pytest", *CREDENTIAL_FREE, "-q", "-n0", "-p", "no:cacheprovider"],
                       cwd=HARNESS, env=env, capture_output=True, text=True, timeout=300)

    assert r.returncode == 0, f"needs credentials:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
