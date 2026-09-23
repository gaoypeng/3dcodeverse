"""A driver's summary survives a pipe larger than the 8 KiB buffer (finish() used to exit before flushing)."""

from __future__ import annotations

import json

import pytest

from codeverse3d.spatial.node import run_node, runtime_js_dir
from tests.scene_runtime.conftest import needs_node

pytestmark = [pytest.mark.node, needs_node]

#: comfortably past the 8 KiB pipe buffer that used to truncate
PAYLOAD_BYTES = 64 * 1024


def _driver(tmp_path, n: int, code: int):
    p = tmp_path / "d.mjs"
    p.write_text(f"""
import {{ finish }} from {json.dumps(str(runtime_js_dir() / 'lib' / 'cli.mjs'))};
finish({{ ok: true, blob: 'x'.repeat({n}), tail: 'LAST' }}, {code});
""")
    return p


@pytest.mark.parametrize("code", [0, 1])
def test_a_large_summary_survives_the_pipe(tmp_path, code: int):
    res = run_node(_driver(tmp_path, PAYLOAD_BYTES, code), [], timeout_s=60, check=False)
    assert res.rc == code
    assert res.last_json is not None, "the summary was truncated mid-JSON"
    assert res.last_json["tail"] == "LAST" and len(res.last_json["blob"]) == PAYLOAD_BYTES

