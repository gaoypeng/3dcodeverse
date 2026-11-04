"""CadQueryRuntime: skeleton + build (real cadquery when importable; fake interpreter otherwise)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.languages.cadquery import CadQueryRuntime
from tests.blender_cadquery.conftest import has_cadquery

needs_cq = pytest.mark.skipif(not has_cadquery(), reason="cadquery not importable")


@needs_cq
def test_skeleton_builds_named_coloured_glb(tmp_ws, table_plan) -> None:
    trimesh = pytest.importorskip("trimesh")
    rt = CadQueryRuntime()
    rt.skeleton(tmp_ws, table_plan)
    assert rt.lint(tmp_ws).passed
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message, r.stderr_tail)
    assert set(r.extra_paths) == {"step", "stl"} and Path(r.extra_paths["step"]).stat().st_size > 0
    scene = trimesh.load(r.glb_path)
    assert set(scene.graph.nodes_geometry) == {"TableTop", "Shelf", "Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-6 and abs(hi[1] - 0.6) < 1e-6  # Y-up, on the ground
    assert abs(lo[2] + 0.25) < 1e-6 and abs(hi[0] - 0.25) < 1e-6
    top = scene.geometry[scene.graph["TableTop"][1]]
    assert top.visual.material.baseColorFactor[0] > top.visual.material.baseColorFactor[2]
    c = r.census
    assert c["result_kind"] == "assembly" and c["n_parts"] == 6 and c["tri_count"] == 6 * 12
    leg = next(p for p in c["parts"] if p["name"] == "Leg_1")
    assert leg["valid"] and abs(leg["volume_m3"] - 0.04 * 0.04 * 0.56) < 1e-9 and leg["bbox_min"][2] == 0.0


@needs_cq
def test_assembly_selectors_and_bare_workplane(tmp_ws) -> None:
    """Sub-assembly locations/colours and trailing selectors in ONE assembly build; then the
    bare-Workplane and no-solid fallbacks.  ``body.faces('>Z')`` / ``.edges('|Z')`` on the stack
    must not become a 2-triangle sheet / nothing."""
    trimesh = pytest.importorskip("trimesh")
    src = '''import cadquery as cq
base = cq.Workplane("XY").box(0.4, 0.4, 0.02).translate((0, 0, 0.01))
peg = cq.Workplane("XY").cylinder(0.1, 0.02).translate((0, 0, 0.05))
result = cq.Assembly(name="Board")
result.add(base, name="Base", color=cq.Color(0.2, 0.2, 0.8))
pegs = cq.Assembly(name="Pegs", loc=cq.Location(cq.Vector(0, 0, 0.02)))
pegs.add(peg, name="Peg_0", loc=cq.Location(cq.Vector(0.1, -0.1, 0)), color=cq.Color(1, 0, 0))
pegs.add(peg, name="Peg_1", loc=cq.Location(cq.Vector(-0.1, 0.1, 0)))
result.add(pegs, name="Pegs", color=cq.Color(0, 1, 0))
body = cq.Workplane("XY").box(0.1, 0.1, 0.01).edges("|Z").fillet(0.002)
result.add(body.faces(">Z"), name="Plate", color=cq.Color("gray"))
result.add(cq.Workplane("XY").box(0.02, 0.02, 0.02).edges("|Z"), name="Leg")
f = cq.Workplane('XY').box(0.1, 0.1, 0.01).faces('>Z').val()
result.add(f, name='Sheet')
'''
    (tmp_ws.src / "model.py").write_text(src)
    rt = CadQueryRuntime()
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    scene = trimesh.load(r.glb_path)
    assert {"Base", "Peg_0", "Peg_1", "Plate", "Leg"} <= set(scene.graph.nodes_geometry)
    parts = {p["name"]: p for p in r.census["parts"]}
    assert parts["Plate"]["n_solids"] == 1 and abs(parts["Plate"]["volume_m3"] - 0.1 * 0.1 * 0.01) < 2e-6
    assert parts["Leg"]["n_solids"] == 1 and parts["Leg"]["tri_count"] == 12
    warns = r.census["build_report"]["warnings"]
    assert any("'Plate'" in w and "Face" in w and "trailing selector" in w for w in warns), warns
    assert any("'Leg'" in w and "Edge" in w for w in warns), warns
    assert parts["Sheet"]["n_solids"] == 0   # a bare cq.Face stays a face but is flagged
    assert any("'Sheet'" in w and "contains no solid" in w for w in warns), warns

    # bare-Workplane result with a trailing selector → single node 'Object' + warning, parent solid exported
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(0.1, 0.1, 0.01).faces('>Z')\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok and r.census["result_kind"] == "single" and r.census["parts"][0]["n_solids"] == 1
    assert trimesh.load(r.glb_path).graph.nodes_geometry == ["Object"]
    # no solid anywhere in the chain (2D wires only) → a typed error naming the stack, not a silent sheet
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nresult = cq.Workplane('XY').rect(0.1, 0.1)\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "ExportError" and "Wire" in r.error_message and "not a Solid" in r.error_message

