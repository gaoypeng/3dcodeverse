"""ThreeJsRuntime.build through runtime_js/export_glb.mjs (needs node)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse3d.languages.threejs import ThreeJsRuntime
from codeverse3d.spatial.measure import measure_glb
from codeverse3d.workspace import Workspace

pytestmark = pytest.mark.node


def _glb_json(glb: str) -> dict:
    raw = Path(glb).read_bytes()
    return json.loads(raw[20:20 + int.from_bytes(raw[12:16], "little")])


def test_build_stool_glb_and_census(stool_ws: Workspace):
    rt = ThreeJsRuntime()
    res = rt.build(stool_ws)
    assert res.ok, (res.error_type, res.error_message, res.stderr_tail)
    assert Path(res.glb_path).is_file() and Path(res.glb_path).stat().st_size > 1000
    assert (stool_ws.artifacts / "build.json").is_file()
    c = res.census  # what only the export knows; the object itself is measured off the GLB
    assert c["tick_present"] is True
    assert c["placement_offset"] is None
    assert c["instanced_meshes_baked"] == 0 and c["selfcheck_ran"] is False
    assert c["unnamed_meshes"] == 0
    m = measure_glb(res.glb_path)
    assert sorted(p.name for p in m.parts) == ["Legs", "Seat", "Stretchers"] and m.tri_count > 500
    assert len(_glb_json(res.glb_path)["materials"]) == 3  # seat + legs + stretchers (distinct objects)
    # the GLB carries named nodes per part and the bbox matches the authored sizes
    scene = trimesh.load(res.glb_path, force="scene")
    names = set(scene.graph.nodes)
    assert {"Stool", "Seat", "Legs", "Stretchers", "Leg_LB", "Leg_RF"} <= names
    assert len(scene.graph.nodes_geometry) == 9
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


def test_build_keeps_source_placement_and_warns(stool_ws: Workspace):
    """An off-ground / off-centre object is exported AS AUTHORED (no silent re-centring):
    the census records the offset it would need and the warning says so."""
    p = stool_ws.src / "object.js"
    p.write_text(p.read_text().replace("return root;", "root.position.set(0.5, 0.2, 0); return root;"))
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok
    off = res.census["placement_offset"]
    assert off is not None and abs(off[0] + 0.5) < 1e-4 and abs(off[1] + 0.2) < 1e-4
    assert any("off ground/centre" in w and "exported as authored" in w for w in res.census["warnings"])
    lo, _ = trimesh.load(res.glb_path, force="scene").bounds
    assert abs(lo[1] - 0.2) < 2e-3 and abs(lo[0] - 0.33) < 2e-3


def test_build_per_plan_placement_passes_contract_even_when_union_is_off_centre(stool_ws: Workspace):
    """Plan parts whose union footprint is NOT centred (backrest-style skew): an object
    built exactly per plan must not be reported as 'centre off' for every part."""
    from codeverse3d.contracts.plan import StaticPlan
    from codeverse3d.spatial.contract import check_contract
    from codeverse3d.spatial.measure import measure_glb

    (stool_ws.src / "object.js").write_text(
        "import * as THREE from 'three';\nexport function build(T) { const g = new THREE.Group(); g.name = 'Sign';\n"
        "  const base = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.35, 0.3), new THREE.MeshStandardMaterial({color: 0x888888}));\n"
        "  base.name = 'Base'; base.position.set(0, 0.175, 0); g.add(base);\n"
        "  const panel = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.1, 0.3), new THREE.MeshStandardMaterial({color: 0x884422}));\n"
        "  panel.name = 'Panel'; panel.position.set(0, 0.4, 0.2); g.add(panel); return g; }\n")
    plan = StaticPlan(
        object_name="Sign", summary="x", overall_bbox={"center": [0, 0.225, 0.1], "extents": [0.5, 0.45, 0.5]},
        parts=[{"name": "Base", "role": "r", "description": "d", "bbox": {"center": [0, 0.175, 0], "extents": [0.4, 0.35, 0.3]}},
               {"name": "Panel", "role": "r", "description": "d", "bbox": {"center": [0, 0.4, 0.2], "extents": [0.5, 0.1, 0.3]}}])
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok and res.census["placement_offset"] == [0, 0, -0.1]
    rep = check_contract(measure_glb(res.glb_path), plan, language="threejs")
    assert rep.passed, [f.message for f in rep.findings]
    assert not [f for f in rep.findings if f.target in ("Base", "Panel")]
    assert [f.message for f in rep.findings if "footprint" in f.message]  # the offset is still reported, once, as WARN


def test_build_bakes_instanced_meshes_for_trimesh(stool_ws: Workspace):
    """InstancedMesh must not export as EXT_mesh_gpu_instancing (trimesh ignores it):
    measure/connectivity must see every instance as its own named mesh."""
    from codeverse3d.spatial.connectivity import check_connectivity

    (stool_ws.src / "object.js").write_text(
        "import * as THREE from 'three';\nexport function build(T) { const g = new THREE.Group(); g.name = 'Fence';\n"
        "  const mat = new THREE.MeshStandardMaterial({ color: 0x885533 });\n"
        "  const posts = new THREE.InstancedMesh(new THREE.BoxGeometry(0.05, 1, 0.05), mat, 10); posts.name = 'Posts';\n"
        "  const m = new THREE.Matrix4();\n"
        "  for (let i = 0; i < 10; i++) { m.makeTranslation(-2.25 + i * 0.5, 0.5, 0); posts.setMatrixAt(i, m); posts.setColorAt(i, new THREE.Color(i % 2 ? 0xff0000 : 0x00ff00)); }\n"
        "  posts.instanceMatrix.needsUpdate = true; g.add(posts);\n"
        "  const rail = new THREE.Mesh(new THREE.BoxGeometry(4.6, 0.05, 0.03), mat); rail.name = 'Rail'; rail.position.set(0, 1.025, 0); g.add(rail);\n"
        "  return g; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok, res.error_message
    assert res.census["instanced_meshes_baked"] == 1
    header = _glb_json(res.glb_path)
    assert "EXT_mesh_gpu_instancing" not in (header.get("extensionsUsed") or [])
    assert len(header["materials"]) == 3  # base + two instance colours
    scene = trimesh.load(res.glb_path, force="scene")
    assert {"Posts_0", "Posts_9", "Rail"} <= set(scene.graph.nodes) and len(scene.graph.nodes_geometry) == 11
    m = measure_glb(res.glb_path)
    assert sorted(p.name for p in m.parts) == ["Posts", "Rail"]
    assert m.tri_count == 132 and abs(m.ground_gap_m) < 1e-3
    posts = next(p for p in m.parts if p.name == "Posts")
    assert np.allclose(posts.bbox_min, [-2.275, 0.0, -0.025], atol=1e-3)
    assert np.allclose(posts.bbox_max, [2.275, 1.0, 0.025], atol=1e-3)
    assert posts.tri_count == 120
    assert check_connectivity(res.glb_path).passed


def test_build_nan_geometry_names_mesh_part_and_file(stool_ws: Workspace):
    """A NaN geometry error must name the mesh + part and route to src/parts/<snake>.js."""
    (stool_ws.src / "parts" / "legs.js").write_text(
        "import * as THREE from 'three';\nexport function buildLegs(T) { const g = new THREE.Group(); g.name = 'Legs';\n"
        "  const m = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0/0, 12), new THREE.MeshStandardMaterial());\n"
        "  m.name = 'LegMesh'; g.add(m); return g; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "ContractError"
    assert "'LegMesh'" in res.error_message and "part 'Legs'" in res.error_message and "NaN" in res.error_message
    assert res.error_file == "src/parts/legs.js"
    assert res.census["part"] == "Legs"
    rec = json.loads((stool_ws.artifacts / "export_error.json").read_text())
    assert rec["error"]["part"] == "Legs"


def test_build_runs_exported_selfcheck(stool_ws: Workspace):
    """`export function selfcheck(THREE, root)` is called by the harness: a throw fails
    the build with the agent's message and the src frame; a passing one is recorded."""
    p = stool_ws.src / "object.js"
    src = p.read_text()
    p.write_text(src + "export function selfcheck(THREE_, root) {\n  const box = new THREE.Box3().setFromObject(root);\n"
                 "  if (box.max.y < 1.0) throw new Error(`too short: ${box.max.y.toFixed(2)}`);\n}\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "SelfCheckError"
    assert res.error_message.startswith("selfcheck(THREE, root) threw: too short: 0.45")
    assert res.error_file == "src/object.js" and res.error_line == 13
    p.write_text(src + "export function selfcheck(THREE_, root) { return true; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok and res.census["selfcheck_ran"] is True


def test_build_async_build_and_texture_strip(stool_ws: Workspace):
    (stool_ws.src / "object.js").write_text(
        "import * as THREE from 'three';\nexport async function build(T) { const g = new THREE.Group(); g.name='Async';\n"
        "  const m = new THREE.Mesh(new THREE.BoxGeometry(0.2,0.2,0.2), new THREE.MeshStandardMaterial({map: new THREE.Texture()}));\n"
        "  m.name='Box'; m.position.y = 0.1; g.add(m); return g; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok, res.error_message
    assert any("texture" in w for w in res.census["warnings"])
    assert "Async" in trimesh.load(res.glb_path, force="scene").graph.nodes  # the awaited group was exported
