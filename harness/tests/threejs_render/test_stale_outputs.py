"""ThreeJsRuntime.build stale-output invalidation (offline — node never runs).

The node-missing raise used to happen AFTER the glb/census wipe but BEFORE any
build.json write, so a previous round's build.json (ok: true) survived a harness
failure and every bare-existence reader treated the old outputs as current.
"""

from __future__ import annotations

import json

import pytest

import codeverse.languages.threejs as rt_mod
from codeverse.languages.threejs import ThreeJsRuntime
from codeverse.spatial.node import NodeError, NodeResult
from codeverse.workspace import Workspace


def _ws_with_stale(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    (ws.src / "object.js").write_text("export function build() {}\n")
    (ws.artifacts / "object.glb").write_bytes(b"stale glb")
    ws.write_json(ws.artifacts / "build.json", {"ok": True})
    ws.write_json(ws.artifacts / "census.json", {"tri_count": 3})
    (ws.artifacts / "export_error.json").write_text("{}")
    return ws


def test_node_missing_raise_leaves_no_stale_outputs(tmp_path, monkeypatch):
    ws = _ws_with_stale(tmp_path)

    def no_node(*a, **k):
        raise NodeError("node binary not found", None)  # harness problem → re-raised

    monkeypatch.setattr(rt_mod, "run_node", no_node)
    with pytest.raises(NodeError):
        ThreeJsRuntime().build(ws)
    for name in ("build.json", "object.glb", "census.json", "export_error.json"):
        assert not (ws.artifacts / name).exists(), name


def test_node_failure_with_result_writes_failed_build_json(tmp_path, monkeypatch):
    ws = _ws_with_stale(tmp_path)

    def timeout(*a, **k):
        raise NodeError("timed out", NodeResult(rc=-9, stdout="", stderr="killed",
                                                last_json=None, duration_ms=5, timed_out=True))

    monkeypatch.setattr(rt_mod, "run_node", timeout)
    res = ThreeJsRuntime().build(ws)
    assert not res.ok and res.error_type == "Timeout"
    assert not (ws.artifacts / "object.glb").exists()
    assert json.loads((ws.artifacts / "build.json").read_text())["ok"] is False
