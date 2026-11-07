"""BlenderRuntime: offline guards + integration tests on the real binary."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from codeverse3d.languages.blender import (
    BlenderRuntime,
)


@pytest.mark.blender
def test_live_skeleton_builds_and_exports_canonical_glb(tmp_ws, table_plan, blender_bin) -> None:
    trimesh = pytest.importorskip("trimesh")
    rt = BlenderRuntime(blender=blender_bin)
    paths = rt.skeleton(tmp_ws, table_plan)
    rels = [p.relative_to(tmp_ws.root).as_posix() for p in paths]
    assert rels == ["src/model.py", "src/parts/table_top.py", "src/parts/leg.py", "src/parts/shelf.py"]
    assert rt.expected_files(table_plan) == rels  # the layout IS the skeleton
    assert rt.lint(tmp_ws).passed
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message, r.stderr_tail)
    assert "[selfcheck]" in r.stdout_tail
    assert Path(r.extra_paths["stl"]).stat().st_size > 0
    scene = trimesh.load(r.glb_path)
    names = set(scene.graph.nodes_geometry)
    assert names == {"TableTop", "Shelf", "Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    assert "Legs" not in scene.graph.nodes  # instances are TOP-LEVEL: no parent Empty node
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-4 and abs(hi[1] - 0.6) < 1e-3  # Y-up, ground contact, height 0.6


@pytest.mark.blender
def test_live_timeout_and_memory_cap(tmp_ws, blender_bin) -> None:
    rt = BlenderRuntime(blender=blender_bin)
    (tmp_ws.src / "model.py").write_text("import bpy, time\nbpy.ops.mesh.primitive_cube_add(size=1)\nwhile True:\n    time.sleep(0.1)\n")
    t0 = time.monotonic()
    r = rt.build(tmp_ws, timeout_s=2)
    assert not r.ok and r.error_type == "BuildTimeout" and time.monotonic() - t0 < 20
    (tmp_ws.src / "model.py").write_text("import bpy\nimport numpy as np\nbpy.ops.mesh.primitive_cube_add(size=1)\nbig = np.ones((3_000_000_000,))\n")
    r = rt.build(tmp_ws, timeout_s=60)
    assert not r.ok and r.error_type == "MemoryError" and r.error_line == 4


@pytest.mark.blender
def test_live_selfcheck_skips_hidden_cutters(tmp_ws, blender_bin) -> None:
    """Audit 2026-09-24 N6a: the skeleton's self-check measured a hidden boolean cutter
    (`Cutter.001`, reaching z = -0.5) and failed a build whose GLB stands exactly on z = 0.
    N6c: it asserts the contract gate's `GROUND_TOL_M`, so a 5 mm lift the gate accepts builds."""
    from codeverse3d.languages.blender import selfcheck_source

    (tmp_ws.src / "model.py").write_text(
        "import bpy\nfrom mathutils import Vector\n" + selfcheck_source() +
        "bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.505)); body = bpy.context.object; body.name = 'Body'\n"
        "bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=2, location=(0, 0, 0.5)); cut = bpy.context.object\n"
        "cut.name = 'Cutter.001'; body.modifiers.new('Hole', 'BOOLEAN').object = cut\n"
        "cut.hide_set(True); cut.hide_render = True\n_selfcheck()\n")
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert "[selfcheck] 1 mesh objects, z_min=0.0050" in r.stdout_tail


@pytest.mark.blender
def test_live_triangle_count_is_not_a_build_error(tmp_ws, blender_bin) -> None:
    """Audit 2026-09-24 N22: a 720k-triangle object failed the Blender build
    (`TriangleBudgetExceeded`) while three.js built it clean; the depth gate is the one ceiling."""
    (tmp_ws.src / "model.py").write_text(
        "import bpy\nbpy.ops.mesh.primitive_grid_add(x_subdivisions=600, y_subdivisions=600, size=1)\n"
        "bpy.context.object.name = 'Sheet'\n")
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert not any("triangle" in w for w in r.census["warnings"])
