"""ThreeJsRuntime.build through runtime_js/export_glb.mjs (needs node)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse.languages.threejs.runtime import ThreeJsRuntime
from codeverse.workspace import Workspace

pytestmark = pytest.mark.node


def test_build_stool_glb_and_census(stool_ws: Workspace):
    rt = ThreeJsRuntime()
    res = rt.build(stool_ws)
    assert res.ok, (res.error_type, res.error_message, res.stderr_tail)
    assert Path(res.glb_path).is_file() and Path(res.glb_path).stat().st_size > 1000
    assert (stool_ws.artifacts / "build.json").is_file()
    c = res.census
    assert c["object_name"] == "Stool"
    assert [p["name"] for p in c["parts"]] == ["Seat", "Legs", "Stretchers"]
    assert c["tri_count"] > 500 and c["n_meshes"] == 9
    assert c["tick_present"] is True
    assert c["normalised_offset"] is None
    assert c["unnamed_meshes"] == 0
    assert "Leg_LB" in c["parts"][1]["children"]
    assert len(c["materials"]) == 3  # seat + legs + stretchers (distinct objects)
    # the GLB carries named nodes per part and the bbox matches the authored sizes
    scene = trimesh.load(res.glb_path, force="scene")
    names = set(scene.graph.nodes)
    assert {"Stool", "Seat", "Legs", "Stretchers", "Leg_LB", "Leg_RF"} <= names
    lo, hi = scene.bounds
    assert np.allclose(lo, [-0.17, 0.0, -0.17], atol=2e-3)
    assert np.allclose(hi, [0.17, 0.45, 0.17], atol=2e-3)


def test_build_runtime_error_maps_to_file_line(stool_ws: Workspace):
    p = stool_ws.src / "parts" / "legs.js"
    p.write_text(p.read_text().replace("const off = 0.13;", "const off = undefinedThing();"))
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok
    assert res.error_type == "ReferenceError"
    assert "undefinedThing" in res.error_message
    assert res.error_file == "src/parts/legs.js" and res.error_line == 6
    assert not (stool_ws.artifacts / "object.glb").exists()
    assert (stool_ws.artifacts / "export_error.json").is_file()
    assert json.loads((stool_ws.artifacts / "build.json").read_text())["ok"] is False


def test_build_syntax_error_located(stool_ws: Workspace):
    p = stool_ws.src / "parts" / "seat.js"
    p.write_text(p.read_text() + "\nlet = 1;\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "SyntaxError"
    assert res.error_file == "src/parts/seat.js" and res.error_line == 13


def test_build_contract_error_no_meshes(stool_ws: Workspace):
    (stool_ws.src / "object.js").write_text("import * as THREE from 'three';\nexport function build(T) { const g = new THREE.Group(); g.name='Empty'; return g; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "ContractError" and "no meshes" in res.error_message


def test_build_missing_build_export(stool_ws: Workspace):
    (stool_ws.src / "object.js").write_text("export const x = 1;\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "ContractError" and "export function build" in res.error_message


def test_build_normalises_and_records_offset(stool_ws: Workspace):
    p = stool_ws.src / "object.js"
    p.write_text(p.read_text().replace("return root;", "root.position.set(0.5, 0.2, 0); return root;"))
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok
    off = res.census["normalised_offset"]
    assert off is not None and abs(off[0] + 0.5) < 1e-4 and abs(off[1] + 0.2) < 1e-4
    assert any("off ground/centre" in w for w in res.census["warnings"])
    lo, _ = trimesh.load(res.glb_path, force="scene").bounds
    assert abs(lo[1]) < 2e-3 and abs(lo[0] + 0.17) < 2e-3


def test_build_async_build_and_texture_strip(stool_ws: Workspace):
    (stool_ws.src / "object.js").write_text(
        "import * as THREE from 'three';\nexport async function build(T) { const g = new THREE.Group(); g.name='Async';\n"
        "  const m = new THREE.Mesh(new THREE.BoxGeometry(0.2,0.2,0.2), new THREE.MeshStandardMaterial({map: new THREE.Texture()}));\n"
        "  m.name='Box'; m.position.y = 0.1; g.add(m); return g; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok, res.error_message
    assert any("texture" in w for w in res.census["warnings"])
    assert res.census["object_name"] == "Async"
