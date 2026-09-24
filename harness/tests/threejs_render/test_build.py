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


def test_build_stool_glb_and_census(stool_build, stool_ws: Workspace):
    res = stool_build  # the session's one stool build
    assert Path(res.glb_path).is_file() and Path(res.glb_path).stat().st_size > 1000
    assert (Path(res.glb_path).parent / "build.json").is_file()
    c = res.census  # what only the export knows; the object itself is measured off the GLB
    assert set(c) == {"placement_offset", "instanced_meshes_baked", "selfcheck_ran", "tick_present",
                      "unnamed_meshes", "warnings", "glb_bytes", "three_revision"}
    assert c["three_revision"] == "182"
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
    # no entry module: the exporter's typed error, with its record on disk
    (stool_ws.src / "object.js").unlink()
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "MissingEntryFile"
    assert (stool_ws.artifacts / "export_error.json").is_file()


@pytest.mark.parametrize("path, find, repl, etype, line", [
    ("parts/legs.js", "const off = 0.13;", "const off = undefinedThing();", "ReferenceError", 6),
    ("parts/seat.js", None, "\nlet = 1;\n", "SyntaxError", 13),
])
def test_build_error_maps_to_file_line(stool_ws: Workspace, path, find, repl, etype, line):
    p = stool_ws.src / path
    p.write_text(p.read_text().replace(find, repl) if find else p.read_text() + repl)
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == etype
    assert res.error_file == f"src/{path}" and res.error_line == line
    assert not (stool_ws.artifacts / "object.glb").exists()
    assert (stool_ws.artifacts / "export_error.json").is_file()
    assert json.loads((stool_ws.artifacts / "build.json").read_text())["ok"] is False


@pytest.mark.parametrize("source, message", [
    ("import * as THREE from 'three';\nexport function build(T) { const g = new THREE.Group(); g.name='Empty'; return g; }\n",
     "no meshes"),
    ("export const x = 1;\n", "export function build"),
])
def test_build_contract_errors(stool_ws: Workspace, source, message):
    (stool_ws.src / "object.js").write_text(source)
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "ContractError" and message in res.error_message


def test_build_keeps_source_placement_and_warns(stool_ws: Workspace):
    """Law 7: an off-ground / off-centre object is exported as authored, with a warning.
    (Same build: an exported selfcheck that passes is run and recorded.)"""
    p = stool_ws.src / "object.js"
    p.write_text(p.read_text().replace("return root;", "root.position.set(0.5, 0.2, 0); return root;")
                 + "export function selfcheck(THREE_, root) { return true; }\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok and res.census["selfcheck_ran"] is True
    off = res.census["placement_offset"]
    assert off is not None and abs(off[0] + 0.5) < 1e-4 and abs(off[1] + 0.2) < 1e-4
    warn = next(w for w in res.census["warnings"] if "off ground/centre" in w)
    assert "exported as authored" in warn and "contract gate" not in warn  # N6b: the gate's tolerance differs
    lo, _ = trimesh.load(res.glb_path, force="scene").bounds
    assert abs(lo[1] - 0.2) < 2e-3 and abs(lo[0] - 0.33) < 2e-3


@pytest.mark.parametrize("bare_root", [False, True])
def test_build_bakes_instanced_meshes_for_trimesh(stool_ws: Workspace, bare_root: bool):
    """InstancedMesh is baked (trimesh ignores EXT_mesh_gpu_instancing) into SIBLING meshes
    ``Posts_0..9``, so the contract gate counts them as the plan's ×10 instances (audit
    2026-09-24 N2: a wrapper Group ``Posts`` was one part, "found 1 instance(s)" + a bbox ERROR)."""
    from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
    from codeverse3d.spatial.connectivity import check_connectivity
    from codeverse3d.spatial.contract import check_contract

    posts = ("  const mat = new THREE.MeshStandardMaterial({ color: 0x885533 });\n"
             "  const posts = new THREE.InstancedMesh(new THREE.BoxGeometry(0.05, 1, 0.05), mat, 10); posts.name = 'Posts';\n"
             "  const m = new THREE.Matrix4();\n"
             "  for (let i = 0; i < 10; i++) { m.makeTranslation(-2.25 + i * 0.5, 0.5, 0); posts.setMatrixAt(i, m); posts.setColorAt(i, new THREE.Color(i % 2 ? 0xff0000 : 0x00ff00)); }\n"
             "  posts.instanceMatrix.needsUpdate = true;\n")
    if bare_root:
        body = posts + "  return posts; }\n"
    else:
        body = ("  const g = new THREE.Group(); g.name = 'Fence';\n" + posts + "  g.add(posts);\n"
                "  const rail = new THREE.Mesh(new THREE.BoxGeometry(4.6, 0.05, 0.03), mat); rail.name = 'Rail'; rail.position.set(0, 1.025, 0); g.add(rail);\n"
                "  return g; }\n")
    (stool_ws.src / "object.js").write_text("import * as THREE from 'three';\nexport function build(T) {\n" + body)
    res = ThreeJsRuntime().build(stool_ws)
    assert res.ok, res.error_message
    assert res.census["instanced_meshes_baked"] == 1
    header = _glb_json(res.glb_path)
    assert "EXT_mesh_gpu_instancing" not in (header.get("extensionsUsed") or [])
    assert len(header["materials"]) == (2 if bare_root else 3)  # (base +) two instance colours
    names = [f"Posts_{i}" for i in range(10)] + ([] if bare_root else ["Rail"])
    m = measure_glb(res.glb_path)
    assert sorted(p.name for p in m.parts) == sorted(names)
    assert m.tri_count == 12 * len(names) and abs(m.ground_gap_m) < 1e-3
    p9 = next(p for p in m.parts if p.name == "Posts_9")
    assert np.allclose(p9.bbox_min, [2.225, 0.0, -0.025], atol=1e-3)
    assert np.allclose(p9.bbox_max, [2.275, 1.0, 0.025], atol=1e-3)
    parts = [PartPlan(name="Posts", role="r", description="d", instances=10,
                      bbox=BBox(center=(0, 0.5, 0), extents=(0.05, 1.0, 0.05)))]
    if not bare_root:
        parts.append(PartPlan(name="Rail", role="r", description="d",
                              bbox=BBox(center=(0, 1.025, 0), extents=(4.6, 0.05, 0.03))))
    plan = StaticPlan(object_name="Fence", summary="s", overall_bbox=BBox(center=(0, 0.525, 0), extents=(4.6, 1.05, 0.05)),
                      parts=parts)
    assert [f.message for f in check_contract(m, plan, language="threejs").findings
            if f.severity.value != "info"] == []
    if not bare_root:
        assert check_connectivity(res.glb_path).passed


def test_build_nan_geometry_names_mesh_part_and_file(stool_ws: Workspace):
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


def test_build_failing_selfcheck_is_a_typed_error(stool_ws: Workspace):
    p = stool_ws.src / "object.js"
    src = p.read_text()
    p.write_text(src + "export function selfcheck(THREE_, root) {\n  const box = new THREE.Box3().setFromObject(root);\n"
                 "  if (box.max.y < 1.0) throw new Error(`too short: ${box.max.y.toFixed(2)}`);\n}\n")
    res = ThreeJsRuntime().build(stool_ws)
    assert not res.ok and res.error_type == "SelfCheckError"
    assert res.error_message.startswith("selfcheck(THREE, root) threw: too short: 0.45")
    assert res.error_file == "src/object.js" and res.error_line == 13
