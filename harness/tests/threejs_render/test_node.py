"""run_node: last-JSON parsing, timeouts, error propagation."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.spatial.node import NodeError, parse_last_json, run_node


def test_parse_last_json_picks_last_object():
    out = 'noise\n{"a": 1}\nmore noise\n{"ok": true, "n": 2}\n[1,2]\n'
    assert parse_last_json(out) == {"ok": True, "n": 2}
    assert parse_last_json("nothing here") is None
    assert parse_last_json('{"broken": \n') is None


def test_missing_script_raises(tmp_path: Path):
    with pytest.raises(NodeError):
        run_node(tmp_path / "nope.mjs", [], timeout_s=5)


@pytest.mark.node
def test_run_node_timeout_kills(tmp_path: Path):
    script = tmp_path / "hang.mjs"
    script.write_text("setInterval(() => {}, 1000); console.log('started');")
    with pytest.raises(NodeError) as ei:
        run_node(script, [], timeout_s=1.5)
    assert ei.value.result is not None and ei.value.result.timed_out


@pytest.mark.node
def test_run_node_env_is_scrubbed_of_secrets(tmp_path: Path, monkeypatch):
    """Generated js (scene.js, agent modules) runs under run_node: no credentials."""
    monkeypatch.setenv("GEMINI_API_KEYS", "k1,k2")
    monkeypatch.setenv("FAKE_SERVICE_TOKEN", "t")
    script = tmp_path / "env.mjs"
    script.write_text(
        "console.log(JSON.stringify({ok: true, gem: process.env.GEMINI_API_KEYS ?? null, "
        "tok: process.env.FAKE_SERVICE_TOKEN ?? null, np: process.env.NODE_PATH ?? null}));")
    rec = run_node(script, [], timeout_s=20).last_json
    assert rec["gem"] is None and rec["tok"] is None
    assert rec["np"] and rec["np"].endswith("node_modules")
