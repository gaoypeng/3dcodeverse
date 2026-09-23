"""A driver's summary survives a pipe, however big it is.

`finish()` used to `process.stdout.write(...)` and then `process.exit()`.  Node's stdout
to a PIPE is asynchronous, and `process.exit` does not flush it, so a summary larger than
the pipe buffer was cut mid-JSON and the caller saw no parsable last line.

Measured 2026-09-06 on the starter scene, `probe_scene.mjs --compile`: written to a file
the summary is 10 462 bytes and parses; through a pipe it was **exactly 8192** and did not.
That is the real mechanism behind every "driver output lost" and "[?] scene did not boot"
in `eval/bench/out/scene_baseline` / `scene_textures` — it depends on how big the census is, not
on how busy the machine is, which is why it read as weather for two batteries.  In
`scene_textures/japanese_garden` it cost the round all three repair attempts and the
texture use the arm existed to measure.
"""

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

