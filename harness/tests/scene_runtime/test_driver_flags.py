"""Every documented driver flag must PARSE."""

from __future__ import annotations

import subprocess

import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node

pytestmark = [pytest.mark.node, needs_node]

# the probe is a raw geometry instrument: it has no post chain to switch off
FLAGS = {
    "render_scene.mjs": ["--no-settle", "--camera-repair", "--no-post"],
    "probe_scene.mjs": ["--no-settle", "--camera-repair"],
}


@pytest.mark.parametrize("driver", sorted(FLAGS))
def test_every_documented_flag_parses(driver):
    s = get_settings()
    node = s.binaries.node or "node"
    out = subprocess.run([node, str(s.runtime_js_dir() / driver), *FLAGS[driver]],
                         capture_output=True, text=True, timeout=60)
    blob = out.stdout + out.stderr
    assert "ERR_PARSE_ARGS" not in blob, blob[-400:]
    assert out.returncode != 0 and "--ws" in blob, blob[-400:]
