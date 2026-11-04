"""ThreeJsRuntime.build never leaves a previous round's outputs looking current (offline)."""

from __future__ import annotations

import pytest

import codeverse3d.languages.threejs as rt_mod
from codeverse3d.languages.threejs import ThreeJsRuntime
from codeverse3d.spatial.node import NodeError
from codeverse3d.workspace import Workspace


def test_no_stale_outputs_survive_a_node_failure(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "run").create()
    (ws.src / "object.js").write_text("export function build() {}\n")
    for name in ("build.json", "object.glb", "census.json", "export_error.json"):
        (ws.artifacts / name).write_text("{}")

    def no_node(*a, **k):
        raise NodeError("node binary not found", None)  # harness problem → re-raised

    monkeypatch.setattr(rt_mod, "run_node", no_node)
    with pytest.raises(NodeError):
        ThreeJsRuntime().build(ws)
    for name in ("build.json", "object.glb", "census.json", "export_error.json"):
        assert not (ws.artifacts / name).exists(), name
