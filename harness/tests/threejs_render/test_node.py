"""run_node: last-JSON parsing, timeouts, error propagation."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.spatial.node import NodeError, parse_last_json, run_node, runtime_js_dir


def test_parse_last_json_picks_last_object():
    out = 'noise\n{"a": 1}\nmore noise\n{"ok": true, "n": 2}\n[1,2]\n'
    assert parse_last_json(out) == {"ok": True, "n": 2}
    assert parse_last_json("nothing here") is None
    assert parse_last_json('{"broken": \n') is None


def test_missing_script_raises(tmp_path: Path):
    with pytest.raises(NodeError):
        run_node(tmp_path / "nope.mjs", [], timeout_s=5)


@pytest.mark.node
def test_run_node_echo(tmp_path: Path):
    script = tmp_path / "echo.mjs"
    script.write_text("console.log('hello'); console.log(JSON.stringify({ok: true, argv: process.argv.slice(2), np: process.env.NODE_PATH}));")
    res = run_node(script, ["--x", "1"], timeout_s=20)
    assert res.rc == 0
    assert res.last_json["argv"] == ["--x", "1"]
    assert res.last_json["np"].endswith("runtime_js/node_modules")
    assert res.duration_ms >= 0


@pytest.mark.node
def test_run_node_timeout_kills(tmp_path: Path):
    script = tmp_path / "hang.mjs"
    script.write_text("setInterval(() => {}, 1000); console.log('started');")
    with pytest.raises(NodeError) as ei:
        run_node(script, [], timeout_s=1.5)
    assert ei.value.result is not None and ei.value.result.timed_out


@pytest.mark.node
def test_run_node_nonzero_exit(tmp_path: Path):
    script = tmp_path / "fail.mjs"
    script.write_text("console.log(JSON.stringify({ok:false, error:{message:'bad thing'}})); process.exit(3);")
    with pytest.raises(NodeError) as ei:
        run_node(script, [], timeout_s=20)
    assert "bad thing" in str(ei.value)
    assert ei.value.result.rc == 3
    res = run_node(script, [], timeout_s=20, check=False)
    assert res.rc == 3 and res.last_json["ok"] is False


@pytest.mark.node
def test_three_hook_resolves_bare_three(tmp_path: Path):
    script = tmp_path / "t.mjs"
    script.write_text("import * as THREE from 'three'; import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';"
                      "console.log(JSON.stringify({rev: THREE.REVISION, rb: typeof RoundedBoxGeometry}));")
    res = run_node(script, [], timeout_s=30, three_hook=True)
    assert res.last_json["rev"] == "182" and res.last_json["rb"] == "function"
    assert (runtime_js_dir() / "lib" / "resolve_three.mjs").is_file()
