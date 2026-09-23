"""Every documented driver flag must PARSE.

Regression: --no-settle / --camera-repair / --auto-exposure were read but never declared
in the parseArgs whitelist, so the first battery to enable them crashed every render.
Each driver runs with all optional flags and no workspace: it must die on OUR
'--ws is required', never in the argument parser.
"""

from __future__ import annotations

import subprocess

import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node

pytestmark = [pytest.mark.node, needs_node]

FLAGS = ["--no-settle", "--camera-repair", "--auto-exposure", "--no-post"]


@pytest.mark.parametrize("driver", ["render_scene.mjs", "probe_scene.mjs"])
def test_every_documented_flag_parses(driver):
    s = get_settings()
    node = s.binaries.node or "node"
    out = subprocess.run([node, str(s.runtime_js_dir() / driver), *FLAGS],
                         capture_output=True, text=True, timeout=60)
    blob = out.stdout + out.stderr
    assert "ERR_PARSE_ARGS" not in blob, blob[-400:]
    assert out.returncode != 0 and "--ws" in blob, blob[-400:]
