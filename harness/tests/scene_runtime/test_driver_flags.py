"""Every documented driver flag must PARSE.

Postmortem 2026-08-30: --no-settle / --camera-repair / --auto-exposure were read
via args[...] but never declared in the parseArgs whitelist, so the first battery
to enable them (scene_px_v1) crashed every render with ERR_PARSE_ARGS_UNKNOWN_OPTION
and four runs' generation cost was lost.  This guard invokes each driver with all
optional flags and no workspace: it must die on OUR '--ws is required' error,
never in the argument parser.
"""

from __future__ import annotations

import subprocess

import pytest

from codeverse.config import get_settings
from tests.scene_runtime.conftest import needs_node

pytestmark = [pytest.mark.node, needs_node]

FLAGS = ["--no-settle", "--camera-repair", "--auto-exposure"]


@pytest.mark.parametrize("driver", ["render_scene.mjs", "probe_scene.mjs"])
def test_every_documented_flag_parses(driver):
    s = get_settings()
    node = s.binaries.node or "node"
    out = subprocess.run([node, str(s.runtime_js_dir() / driver), *FLAGS],
                         capture_output=True, text=True, timeout=60)
    blob = out.stdout + out.stderr
    assert "ERR_PARSE_ARGS" not in blob, blob[-400:]
    assert out.returncode != 0 and "--ws" in blob, blob[-400:]
