"""run_node: last-JSON parsing, error propagation, the scrubbed env."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.spatial.node import run_node


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
