"""`CV3D_` was the settings prefix until D78 (2026-09-22); a shell that still sets it keeps working."""

from __future__ import annotations

import os
import subprocess
import sys


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    code = ("import os, codeverse3d; "
            "print(os.environ.get('C3D_RUNS_DIR', '-'), os.environ.get('C3D_RENDER__GPU', '-'))")
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("CV3D_", "C3D_"))}
    return subprocess.run([sys.executable, "-c", code], env={**clean, **env}, capture_output=True, text=True, check=True)


def test_an_old_prefix_is_read_as_the_new_one_warned_and_loses_to_the_new_name():
    p = _run({"CV3D_RUNS_DIR": "/tmp/old", "CV3D_RENDER__GPU": "off"})
    assert p.stdout.split() == ["/tmp/old", "off"]
    assert "CV3D_RENDER__GPU" in p.stderr and "CV3D_RUNS_DIR" in p.stderr
    p = _run({"CV3D_RUNS_DIR": "/tmp/old", "C3D_RUNS_DIR": "/tmp/new"})
    assert p.stdout.split()[0] == "/tmp/new" and p.stderr == ""
