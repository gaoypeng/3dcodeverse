"""ThreeJsRuntime.build never leaves a previous round's outputs looking current (offline)."""

from __future__ import annotations

import json

import pytest

import codeverse3d.languages.threejs as rt_mod
from codeverse3d.languages.threejs import ThreeJsRuntime
from codeverse3d.spatial.node import NodeError, NodeResult
from codeverse3d.workspace import Workspace


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
    assert not res.ok and res.error_type == "BuildTimeout"
    assert not (ws.artifacts / "object.glb").exists()
    assert json.loads((ws.artifacts / "build.json").read_text())["ok"] is False
