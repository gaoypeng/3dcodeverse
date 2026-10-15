"""The ``placeholder_part`` contract ERROR (D110): a plan part still the skeleton's box.

Pure rule on synthetic measurements, then each object language's REAL skeleton built and measured:
every part fires; a part rebuilt as real geometry at the same plan box does not."""

from __future__ import annotations

import shutil

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import (
    PLACEHOLDER_PART,
    GateFinding,
    GateReport,
    Measurement,
    PartMeasure,
    Severity,
)
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.orchestrator import build_refine_instructions
from codeverse3d.spatial.contract import (
    PLACEHOLDER_MAX_TRIS,
    check_contract,
    is_placeholder,
    plan_bbox_to_glb,
)
from codeverse3d.workspace import Workspace


def _plan() -> StaticPlan:
    return StaticPlan(
        object_name="SideTable", summary="A small side table.",
        overall_bbox=BBox(center=(0, 0, 0.3), extents=(0.5, 0.5, 0.6)),
        parts=[
            PartPlan(name="TableTop", role="top", description="slab", bbox=BBox(center=(0, 0, 0.58), extents=(0.5, 0.5, 0.04))),
            PartPlan(name="Leg", role="support", description="leg", bbox=BBox(center=(0.21, 0.21, 0.28), extents=(0.04, 0.04, 0.56)),
                     attach_to="TableTop", symmetry="mirror_x", instances=4),
            PartPlan(name="Shelf", role="shelf", description="round shelf",
                     bbox=BBox(center=(0, 0, 0.15), extents=(0.42, 0.42, 0.02)), attach_to="Leg"),
        ],
        acceptance=[],
    )


def _row(name: str, box: BBox, tris: int, dx: float = 0.0, fill: float | None = 1.0) -> PartMeasure:
    """A measured row at ``box``; ``fill`` = volume / box volume (None = not watertight)."""
    lo = [c - e / 2 for c, e in zip(box.center, box.extents, strict=True)]
    hi = [c + e / 2 for c, e in zip(box.center, box.extents, strict=True)]
    vol = None if fill is None else fill * box.extents[0] * box.extents[1] * box.extents[2]
    return PartMeasure(name=name, bbox_min=(lo[0] + dx, lo[1], lo[2]), bbox_max=(hi[0] + dx, hi[1], hi[2]), tri_count=tris,
                       watertight=fill is not None, volume_m3=vol)


def test_rule_is_triangles_and_the_plan_box_to_a_millimetre():
    box = plan_bbox_to_glb(_plan().parts[2].bbox, "blender")
    assert is_placeholder([_row("Shelf", box, PLACEHOLDER_MAX_TRIS)], box)
    assert not is_placeholder([_row("Shelf", box, 96)], box)                    # real geometry, same box
    assert not is_placeholder([_row("Shelf", box, 12, dx=0.002)], box)          # moved 2 mm: someone built it
    assert not is_placeholder([_row("Shelf", box, 0)], box)                     # tris not measured
    # a real 12-triangle hexahedron at its plan box: the tapered cadquery fretboard of
    # loop_argon_static iter_01 mus_med_acoustic_guitar (fill 0.879), a wedge, an open pane
    assert not is_placeholder([_row("Shelf", box, 12, fill=0.879)], box)
    assert not is_placeholder([_row("Shelf", box, 8, fill=0.5)], box)
    assert not is_placeholder([_row("Shelf", box, 2, fill=None)], box)
    assert not is_placeholder([], box)
    # instances: the copies sit wherever the symmetry put them — only their size is the plan's
    leg = plan_bbox_to_glb(_plan().parts[1].bbox, "blender")
    copies = [_row(f"Leg_{i}", leg, 12, dx=-0.42 * (i % 2)) for i in range(4)]
    assert is_placeholder(copies, leg)
    assert not is_placeholder(copies[:3] + [_row("Leg_3", leg, 200)], leg)  # one real leg: being built


def test_a_bare_box_built_exactly_to_the_plan_is_taken_for_the_placeholder():
    """The known limit, pinned: measurement alone cannot tell a real bare 12-triangle box at the
    plan's millimetre from the skeleton's box, so it fires.  Over the loop_argon iter_00/01 corpus
    (942 part rows) no such real part existed — every hit was a byte-unchanged skeleton file."""
    box = plan_bbox_to_glb(_plan().parts[0].bbox, "threejs")
    assert is_placeholder([_row("TableTop", box, 12)], box)
    assert not is_placeholder([_row("TableTop", box, 20)], box)  # the same box, segmented once


def test_gate_finding_is_typed_and_routes_first():
    plan = _plan()
    rows = [_row("TableTop", plan_bbox_to_glb(plan.parts[0].bbox, "blender"), 400),
            *[_row(f"Leg_{i}", plan_bbox_to_glb(plan.parts[1].bbox, "blender"), 12, dx=-0.42 * (i % 2)) for i in range(4)],
            _row("Shelf", plan_bbox_to_glb(plan.parts[2].bbox, "blender"), 12)]
    lo, hi = (-0.25, 0.0, -0.25), (0.25, 0.6, 0.25)
    m = Measurement(bbox_min=lo, bbox_max=hi, extents=(0.5, 0.6, 0.5), center=(0, 0.3, 0), tri_count=472,
                    n_meshes=6, n_islands=6, parts=rows)
    report = check_contract(m, plan, language="blender")
    hits = [f for f in report.findings if f.data.get("kind") == PLACEHOLDER_PART]
    assert {f.target for f in hits} == {"Leg", "Shelf"} and all(f.severity is Severity.ERROR for f in hits)
    assert not report.passed and "placeholder box" in hits[0].message and "planned size" in hits[0].fix_hint
    floating = GateReport.of("connectivity", [GateFinding(gate="connectivity", severity=Severity.ERROR,
                                                          target="TableTop", message="floats")])
    tasks = build_refine_instructions(None, [floating, report], [], plan)
    assert [t.target for t in tasks[:2]] == ["Leg", "Shelf"] and tasks[2].target == "TableTop"


# ----------------------------------------------------------------------------- each real skeleton
def _placeholders(glb: str, language: str) -> set[str]:
    from codeverse3d.spatial.measure import measure_glb

    report = check_contract(measure_glb(glb), _plan(), language=language)
    return {f.target for f in report.findings if f.data.get("kind") == PLACEHOLDER_PART}


def _edit(path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new))


@pytest.mark.blender
def test_blender_skeleton_fires_and_a_built_part_does_not(tmp_path):
    b = get_settings().resolve_blender()
    if not b or (not shutil.which(b) and not b.startswith("/")):
        pytest.skip("no Blender binary")
    from codeverse3d.languages.blender import BlenderRuntime

    ws, rt = Workspace(tmp_path / "b").create(), BlenderRuntime(blender=b)
    rt.skeleton(ws, _plan())
    r = rt.build(ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert _placeholders(r.glb_path, "blender") == {"TableTop", "Leg", "Shelf"}
    _edit(ws.src / "parts" / "shelf.py", 'return add_box("Shelf", SHELF_CENTER, SHELF_EXTENTS, mat)',
          'bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.21, depth=0.02, location=SHELF_CENTER)\n'
          '    obj = bpy.context.object\n    obj.name = "Shelf"\n    obj.data.materials.append(mat)\n    return obj')
    r = rt.build(ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert _placeholders(r.glb_path, "blender") == {"TableTop", "Leg"}


def test_cadquery_skeleton_fires_and_a_built_part_does_not(tmp_path):
    pytest.importorskip("cadquery")
    from codeverse3d.languages.cadquery import CadQueryRuntime

    ws, rt = Workspace(tmp_path / "c").create(), CadQueryRuntime()
    rt.skeleton(ws, _plan())
    r = rt.build(ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert _placeholders(r.glb_path, "cadquery") == {"TableTop", "Leg", "Shelf"}
    _edit(ws.src / "model.py", 'cq.Workplane("XY").box(0.420, 0.420, 0.020)',
          'cq.Workplane("XY").box(0.420, 0.420, 0.020).edges("|Z").fillet(0.02)')
    r = rt.build(ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert _placeholders(r.glb_path, "cadquery") == {"TableTop", "Leg"}


def test_cadquery_tapered_extrude_at_its_plan_box_does_not_fire(tmp_path):
    """A real tapered part (a fretboard: trapezoid outline extruded) is 12 triangles and sits
    exactly on its plan box, yet does not fill it — it was built, not left as the skeleton."""
    pytest.importorskip("cadquery")
    from codeverse3d.languages.cadquery import CadQueryRuntime

    ws, rt = Workspace(tmp_path / "f").create(), CadQueryRuntime()
    rt.skeleton(ws, _plan())
    _edit(ws.src / "model.py", 'cq.Workplane("XY").box(0.420, 0.420, 0.020)',
          'cq.Workplane("XY").polyline([(-0.21, -0.21), (0.21, -0.21), (0.15, 0.21), (-0.15, 0.21)])'
          '.close().extrude(0.020).translate((0, 0, -0.010))')
    r = rt.build(ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    from codeverse3d.spatial.measure import measure_glb

    shelf = [p for p in measure_glb(r.glb_path).parts if p.name.lower() == "shelf"]
    assert shelf and shelf[0].tri_count <= PLACEHOLDER_MAX_TRIS  # the case the fill rule exists for
    assert _placeholders(r.glb_path, "cadquery") == {"TableTop", "Leg"}


@pytest.mark.node
def test_threejs_skeleton_fires_and_a_built_part_does_not(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not available")
    from codeverse3d.languages.threejs import ThreeJsRuntime

    ws, rt = Workspace(tmp_path / "t").create(), ThreeJsRuntime()
    rt.skeleton(ws, _plan())
    r = rt.build(ws)
    assert r.ok, (r.error_type, r.error_message)
    assert _placeholders(r.glb_path, Language.THREEJS.value) == {"TableTop", "Leg", "Shelf"}
    _edit(ws.src / "parts" / "shelf.js", "new THREE.BoxGeometry(size[0], size[1], size[2])",
          "new THREE.BoxGeometry(size[0], size[1], size[2], 4, 1, 4)")
    r = rt.build(ws)
    assert r.ok, (r.error_type, r.error_message)
    assert _placeholders(r.glb_path, Language.THREEJS.value) == {"TableTop", "Leg"}
